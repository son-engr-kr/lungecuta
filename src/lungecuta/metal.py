"""An OpenColorIO processor run on the GPU: OCIO's own shader for it, in Metal Shading
Language, compiled through PyTorch's MPS backend and applied to a frame at once
(04-color.md, Faster paths).

Only processors whose shader reads 1D tables by nearest texel are taken (the ACES 2.0
output views and their inverses, whose tables OCIO interpolates in the shader's own math):
such a table becomes a buffer read at the texel the sampler would return, so the shader
computes what OCIO wrote for the GPU. Anything else (3D LUTs, tables sampled linearly by
the texture unit, dynamic properties) stays on the CPU (`supported`): in the built-in
config, the HDR displays' PQ and HLG curves, which are tables sampled linearly. Metal's
fast math is turned off, so pow, exp and log are the precise ones."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
import PyOpenColorIO as ocio

# One frame on the GPU at a time: PyTorch's MPS backend is not documented as safe to drive
# from several threads, and a render's workers share one processor.
_LOCK = threading.Lock()


def _buffer_read(name: str, channels: int, width: int, match: re.Match) -> str:
    """`NAME.sample(coord).swizzle` as a read of the texel a nearest sampler returns."""
    coord, swizzle = match.group(1), match.group(2)
    index = f"clamp(int(floor(({coord}) * float({width}))), 0, {width - 1})"
    if channels == 1:
        return f"float4({name}[{index}], 0.0, 0.0, 1.0).{swizzle}"
    return f"float4({name}[3 * {index}], {name}[3 * {index} + 1], {name}[3 * {index} + 2], 1.0).{swizzle}"


def _shader(processor: ocio.Processor) -> ocio.GpuShaderDesc:
    desc = ocio.GpuShaderDesc.CreateShaderDesc(language=ocio.GPU_LANGUAGE_MSL_2_0, functionName="OCIOMain")
    processor.getDefaultGPUProcessor().extractGpuShaderInfo(desc)
    return desc


def supported(processor: ocio.Processor) -> bool:
    """Whether `processor` is one this module runs: no 3D LUTs, no dynamic properties, and
    every table 1D and read by nearest texel."""
    desc = _shader(processor)
    return (not list(desc.get3DTextures()) and not list(desc.getUniforms())
            and all(texture.interpolation == ocio.INTERP_NEAREST and texture.height == 1
                    and texture.dimensions == ocio.GpuShaderDesc.TEXTURE_1D for texture in desc.getTextures()))


def kernel_source(processor: ocio.Processor) -> tuple[str, list[np.ndarray]]:
    """The Metal source of a compute kernel `ocio_apply(rgb, tables...)` that transforms
    packed float32 RGB in place, and the tables it reads, in the kernel's buffer order."""
    desc = _shader(processor)
    assert not list(desc.get3DTextures()), "a processor with 3D LUTs is not run on the GPU"
    assert not list(desc.getUniforms()), "a processor with dynamic properties is not run on the GPU"
    source = desc.getShaderText()
    textures = list(desc.getTextures())
    tables = []
    for texture in textures:
        assert texture.interpolation == ocio.INTERP_NEAREST, f"{texture.textureName} is sampled with interpolation"
        assert texture.dimensions == ocio.GpuShaderDesc.TEXTURE_1D and texture.height == 1, \
            f"{texture.textureName} is not a 1D table"
        name, width = texture.textureName, texture.width
        channels = 1 if texture.channel == ocio.GpuShaderDesc.TEXTURE_RED_CHANNEL else 3
        values = np.asarray(texture.getValues(), dtype=np.float32)
        assert values.size == width * channels, f"{name}: {values.size} values for {width} texels"
        tables.append(values)
        # the texture and its sampler, wherever OCIO's class passes, stores and uses them
        source = source.replace(f"texture1d<float> {name}", f"constant float* {name}")
        source = source.replace(f"this->{name}Sampler = {name}Sampler;", "")
        source = re.sub(rf"\n\s*,\s*sampler {name}Sampler", "", source)
        source = re.sub(rf"\n\s*sampler {name}Sampler;", "", source)
        source = re.sub(rf"\n\s*,\s*{name}Sampler\b", "", source)
        source = source.replace(f"{name}.sample({name}Sampler, ", f"{name}.sample(")
        source = re.sub(rf"{name}\.sample\(\s*,?\s*([^;]*?)\)\.(\w+)",
                        lambda match, n=name, c=channels, w=width: _buffer_read(n, c, w, match), source)
    left = [line for line in source.splitlines() if ".sample(" in line or "sampler " in line or "texture1d" in line]
    assert not left, f"texture reads not rewritten: {left[:3]}"
    arguments = "".join(f", constant float* {t.textureName} [[buffer({k + 1})]]" for k, t in enumerate(textures))
    passed = "".join(f"{t.textureName}, " for t in textures)
    kernel = (
        "#pragma METAL fp math_mode(safe)\n#include <metal_stdlib>\nusing namespace metal;\n" + source + f"""
kernel void ocio_apply(device float* rgb [[buffer(0)]]{arguments}, uint i [[thread_position_in_grid]]) {{
    float4 out = OCIOMain({passed}float4(rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2], 1.0));
    rgb[3 * i] = out.r;
    rgb[3 * i + 1] = out.g;
    rgb[3 * i + 2] = out.b;
}}
""")
    return kernel, tables


@dataclass
class GpuProcessor:
    """An OCIO processor whose frames are transformed on the GPU (`apply`); `cpu` is the
    same transform on the CPU, which the tests and `bench/ocio_gpu.py` measure it against."""
    processor: ocio.Processor
    cpu: ocio.CPUProcessor
    _compiled: Any = None
    _tables: list | None = None

    def _ready(self):
        import torch  # imported here: a command that shows no frame does not pay for it
        assert torch.backends.mps.is_available(), "OCIO's GPU path needs Metal (PyTorch's MPS backend)"
        if self._compiled is None:
            source, tables = kernel_source(self.processor)
            self._compiled = torch.mps.compile_shader(source)
            self._tables = [torch.from_numpy(table).to("mps") for table in tables]
        return torch

    def apply(self, image: np.ndarray) -> None:
        """Transform a C-contiguous float32 array of RGB triples in place."""
        assert image.dtype == np.float32 and image.flags.c_contiguous and image.shape[-1] == 3
        if image.size == 0:
            return
        with _LOCK:
            torch = self._ready()
            values = torch.from_numpy(image.reshape(-1)).to("mps")
            self._compiled.ocio_apply(values, *self._tables, threads=image.size // 3)
            image.reshape(-1)[:] = values.cpu().numpy()
