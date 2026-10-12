# Colour pipeline: primary sources (retrieved 2026-10-10)

Measurements were made on macOS 26.4.1 (25E253).

**Labels used below**
- **VERIFIED**: I read it in the primary source and quote it.
- **VERIFIED (mirror)**: the vendor's own document, read from a third-party copy because the vendor URL blocks automated downloads or needs a login. I checked the PDF metadata.
- **MEASURED**: confirmed by a local test on this Mac (macOS 26.4.1, build 25E253). The vendor does not document it.
- **UNVERIFIED**: not confirmed in a primary source.

Matrix convention: ITU, DJI, Blackmagic and OCIO matrices use column vectors (`XYZ = M·RGB`). ACES CTL `mult_f3_f33` uses row vectors, so the CTL matrices quoted below must be transposed before use with column vectors.

---

## 1. Sony S-Log3 with S-Gamut3.Cine and S-Gamut3

**Sources**
- Sony, "Technical Summary for S-Gamut3.Cine/S-Log3 and S-Gamut3/S-Log3", V1.01. PDF metadata: 2014-09-23, 7 pages.
  - Official URL: https://pro.sony/s3/cms-static-content/uploadfile/06/1237494271406.pdf (returns HTTP 403 to automated clients).
  - Read from this mirror: http://starcentral.ca/forums/LUTs/TechnicalSummary_for_S-Gamut3Cine_S-Gamut3_S-Log3_V1_01.pdf
- ACES transform marked "Provided by Sony Electronics Corp.": https://github.com/aces-aswf/aces-input-and-colorspaces/blob/main/sony/CSC.Sony.SLog3_SGamut3Cine_to_ACES.ctl

**Curve, verbatim from Sony**
```
Scene Linear Reflection to S-Log3
If in >= 0.01125000
  out = (420.0 + log10((in + 0.01) / (0.18 + 0.01)) * 261.5) / 1023.0
else
  out = (in * (171.2102946929 – 95.0)/0.01125000 + 95.0) / 1023.0
  in = reflection ; reflection = IRE * 0.9 ; out 0.0 - 1.0 ; 10bit code = Round(out * 1023.0)

S-Log3 to Scene Linear Reflection
If in >= 171.2102946929 / 1023.0
  out = (10.0 ^ ((in * 1023.0 ‐ 420.0) / 261.5)) * (0.18 + 0.01) ‐ 0.01
else
  out + (in * 1023.0 – 95.0) * 0.01125000 / (171.2102946929 – 95.0)
```
The "out +" in the last line is in Sony's original; it is a typo for "out =". The ACES transform has it as "=".

**Code value table (Sony)**

| Curve | 0% black | 18% grey | 90% white |
|---|---|---|---|
| S-Log3 | 3.5%, CV 95 | 41%, CV 420 | 61%, CV 598 |
| S-Log2 | 90 | 347 | 582 |
| S-Log | 90 | 394 | 636 |

**Primaries (Sony table, x y)**
```
S-Gamut3.Cine     R 0.76600 0.27500  G 0.22500 0.80000  B 0.08900 -0.08700  White(D65) 0.31270 0.32900
S-Gamut/S-Gamut3  R 0.73000 0.28000  G 0.14000 0.85500  B 0.10000 -0.05000  White(D65) 0.31270 0.32900
```

**Matrices**
- The Sony section is titled "Color Primaries and conversion matrix" but it contains no RGB-to-XYZ matrix.
- The values below are derived from the primaries by colour-science 0.4.7. They are not published by Sony.
```
S-Gamut3.Cine RGB→XYZ: [0.59908392 0.24892552 0.10244649; 0.21507582 0.8850685 -0.10014432; -0.03206585 -0.02765839 1.14878199]
S-Gamut3      RGB→XYZ: [0.70648271 0.12880105 0.11517216; 0.27097967 0.78660641 -0.05758608; -0.00967785 0.00460004 1.09413556]
```
- The ACES transform adapts the white point to ACES AP0 with CAT02: `calculate_rgb_to_rgb_matrix(SONY_SGAMUT3_CINE_PRI, AP0, CONE_RESP_MAT_CAT02)`.

**Range (Sony FAQ Q4)**: "S-Log3 is recorded as Full range in XAVC, MPEG and HDCAM SR File … Data range is used from 0 to 1023." This document predates the Alpha and FX cameras. Whether their XAVC S/HS files are full or limited range is UNVERIFIED, so read `color_range` from each file.

**Status**: curve and primaries VERIFIED (mirror). Matrices are not published by Sony (derived only).

## 2. Sony S-Log2 with S-Gamut

**Sources**
- Sony, "S-Log2 Technical Paper" V1.0, Jun/6/2012. I found no Sony URL. Read from the copy cited by colour-science: https://drive.google.com/file/d/1Q1RYri6BaxtYYxX0D4zVD6lAmbwmgikc/view
- Sony-provided ACES transforms (now in the repo's archive folder): https://github.com/aces-aswf/aces-input-and-colorspaces/tree/main/archive/sony

**Curve, verbatim, IRE base**
```
Scene Linear to S-Log2:
  x >= 0 : y = ( 0.432699 * log10(155.0 * x / 219.0 + 0.037584 ) + 0.616596 ) + 0.03
  x < 0  : y = x * 3.53881278538813 + 0.030001222851889303
S-Log2 to Scene Linear:
  x >= 0.030001222851889303 : y = 219.0 * (power( 10.0, ( ( x – 0.616596 – 0.03 ) / 0.432699 ) ) – 0.037584) /155.0
  x <  0.030001222851889303 : y = ( x – 0.030001222851889303 ) / 3.53881278538813
Reflection = IRE * 0.9
10-bit: x>=90: y = 219.0*(power(10.0,(((x–64)/(940-64) – 0.616596 – 0.03)/0.432699)) – 0.037584)/155.0
        x<90 : y = ((x-64)/(940-64) – 0.030001222851889303)/3.53881278538813
CV = WV*IRE/100 + Black ; 10-bit Black 64, White 940, WV 876
```

**Gamut**
- From the S-Log3 summary: "The color primaries … of S-Gamut3 are the same as conventional S-Gamut. Current S-Gamut needs color temperature setting to select color conversion matrix."
- Sony's ACES matrices, row-vector convention:
```
SGAMUT_DAYLIGHT_TO_ACES_MTX = {{0.8764457030, 0.0774075345, 0.0573564351},{0.0145411681, 0.9529571767, -0.1151066335},{0.1090131290, -0.0303647111, 1.0577501984}}
SGAMUT_TUNG_TO_ACES_MTX     = {{1.0110238740, 0.1011994504, 0.0600766530},{-0.1362526051, 0.9562196265, -0.1010185315},{0.1252287310, -0.0574190769, 1.0409418785}}
```

**Status**: curve VERIFIED (mirror). Matrices VERIFIED (Sony-provided ACES transforms).

## 3. DJI D-Log and D-Gamut

**Sources**
- DJI white paper, Rev 1.0, 2017-09-29: https://dl.djicdn.com/downloads/zenmuse+x7/20171010/D-Log_D-Gamut_Whitepaper.pdf
- Zenmuse X9 edition, 2022.02: https://dl.djicdn.com/downloads/DJI_Ronin_4D/X9_D_Log_D_Gamut_Whitepaper.pdf

**Curve, verbatim (the reverse conversion mixes "in" and "x" as printed)**
```
in <= 0.0078 : out = 6.025 * in + 0.0929;
in >  0.0078 : out = (log10(in * 0.9892 + 0.0108)) * 0.256663 + 0.584555;
reverse:
in <= 0.14 : out = (x - 0.0929) / 6.025;
in >  0.14 : out = (10^(3.89616 * x - 2.27752) - 0.0108)/0.9892;
10-bit: 0% → 95, 18% → 408, 90% → 586. "encodes 7.8 stops above 18% gray (4200%) to 1.0"
```

**Gamut**
```
D-Gamut  R 0.71 0.31  G 0.21 0.88  B 0.09 -0.08  White (D65) 0.3127 0.3290
RGB→XYZ  [0.6482 0.1940 0.1082; 0.2830 0.8132 -0.0962; -0.0183 -0.0832 1.1903]
XYZ→RGB  [1.7257 -0.4314 -0.1917; -0.6025 1.3906 0.1671; -0.0156 0.0905 0.8489]
D-Gamut→Rec.709  [1.6746 -0.5797 -0.0949; -0.0981 1.3340 -0.2359; -0.0410 -0.2430 1.2840]
Rec.709→D-Gamut  [0.6163 0.2857 0.0980; 0.0505 0.7990 0.1505; 0.0292 0.1604 0.8104]
```

**X9 edition**
- "The conversion function from a linear signal to D-Log remains the same. The only thing that changes is the maximum output level, namely clipping level."
- The clip is 10-bit code 948 at native EI, falling to 789 at EI 200.

**Implementations differ slightly from the paper**
- The ACES transform uses the white-paper constants with CAT02 adaptation.
- The OCIO studio config (2.5 and later) uses a `LogCameraTransform` with `log_side_slope 0.256662970719888`, `log_side_offset 0.58455504907396`, `lin_side_break 0.00758078675` and no `linear_slope`.
- In OCIO, an unset `linear_slope` defaults to the slope of the log curve at the break point. OCIO's toe segment is therefore not exactly the paper's 6.025·in + 0.0929.

**Status**: VERIFIED.

## 4. DJI D-Log M

**Curve and gamut**: I found none published.
- Both DJI white papers (2017, 2022) cover D-Log and D-Gamut only.
- https://www.dji.com/lut lists no D-Log M document.
- **UNVERIFIED** (a negative cannot be proven). Forum posts say DJI never released it.

**Official LUTs (downloaded and inspected)**
- Every D-Log M LUT on https://www.dji.com/lut converts to Rec.709 only; some are titled "vivid". There is no DJI D-Log M LUT to HLG, PQ, ACES or DWG. The only HLG LUT is for Zenmuse X9 D-Log (not M) to Rec.2020 HLG.
- Format: DaVinci Resolve `.cube`, `LUT_3D_SIZE 33` (35,937 entries), no DOMAIN lines (so input 0-1). Output values range from 0 to 1, although the header says "#Not-Clipped."
- Twelve product downloads are byte-identical (MD5 `1993aa7288033b31afd7a621b0b07c86`), with header `# Mavic 3 Pro, D-Log M, 2023-03-24` / `#Generated by LUT Editor.` The products are:
  - Drones: Mavic 3, Mini 4 Pro, Air 3, Air 3S, Avata 2, Flip, O4 Air Unit
  - Handheld: Osmo Pocket 3, Osmo Action 4, Osmo Action 5 Pro, Osmo 360, Osmo Nano
  - The Action 4 and Nano pages are titled "vivid" but serve this same file.
- Osmo Action 6 serves a different file (MD5 `c2ee3cdc…`, 33³, no comment lines).
- The older Mavic 2 / Air 2S `https://docs.djicdn.com/Products+info/DLog-M+to+Rec709.cube` is also different (33³).

**Status**: LUT facts VERIFIED by download. "Never published" is UNVERIFIED.

## 4b. DJI D-Log 2 (added 2026-10-11)

**What is known.** The Osmo Pocket 4P (announced 2026) records a 10-bit "D-Log 2" profile,
available only at 1x zoom; DJI and retailers quote up to 17 stops of dynamic range for it.
- https://www.photographytalk.com/dji-osmo-pocket-4p-revealed-at-cannes-what-we-know/
- https://ibc.org/production/news/dji-launches-dual-lens-osmo-pocket-4p-gimbal-camera/22794
- https://www.cathayphoto.com.sg/cameras/gimbal-cameras/dji-osmo-pocket-4p (footnote: 17 stops in D-Log 2)

**What is not published.** No DJI white paper or colour-space document for D-Log 2 was
found. https://www.dji.com/lut, fetched 2026-10-11, lists no Osmo Pocket 4P entry
(handheld section: Osmo Pocket 3, D-Log M to Rec.709 only). **UNVERIFIED** (a negative).

**Third-party claims, UNVERIFIED.**
- gamut.io (a LUT vendor): DJI "does not seem to be using D-Gamut for D-Log2" and "will be
  releasing a new Color Space"; DaVinci Resolve has no colour space transform for D-Log 2;
  DJI's D-Log and D-Log 2 LUTs do not match on a chart. https://gamut.io/camera/osmo-pocket-4p/ ,
  https://gamut.io/osmo-pocket-4p-luts-dont-match/ (the D-Log 2 grading article returned 403).
- A Zhihu article says DJI's help centre lists D-Log 2 restoration LUTs for the Pocket 4P,
  and that D-Log 2 has no public mathematical definition. The download page itself was not
  found. https://www.zhihu.com/tardis/jm/art/2052391971612897298

**Status.** Curve and gamut: not published as far as found. Official LUT: existence
claimed by one source, not located.

## 5. Apple Log and Apple Log 2

**Sources**
- Apple, "Apple Log Profile White Paper", September 2023, Part 028-00768.
  - Official location (needs Apple Developer sign-in): https://developer.apple.com/download/all/?q=Apple%20log%20profile
  - Read from this copy: https://github.com/thatcherfreeman/aces-transforms/blob/master/specs/Apple_Log_Profile_White_Paper.pdf (PDF Author "Apple Inc.", created 2023-09-05).

**Apple Log curve (from the PDF)**
```
P = γ·log2(R + β) + δ     if R ≥ Rt
P = c·(R − R0)²           if R0 ≤ R < Rt
P = 0                     if R < R0
R = 2^((P − δ)/γ) − β     if P ≥ Pt
R = √(P/c) + R0           if 0 ≤ P < Pt
R = R0                    if P < 0
Pt = c·(Rt − R0)²
R0 = −0.05641088  Rt = 0.01  c = 47.28711236  β = 0.00964052  γ = 0.08550479  δ = 0.69336945
Table: 0% → 0.150477 (10-bit full 154); 18% → 0.488272 (500); 90% → 0.681686 (697); 1200% → 1.0 (1023)
Primaries: BT.2020-2, R 0.708 0.292, G 0.170 0.797, B 0.131 0.046, D65 0.3127 0.3290
Y = 0.2627R + 0.6780G + 0.0593B ; CB = (B−Y)/1.8814 ; CR = (R−Y)/1.4746
```

**Apple Log 2 (iPhone 17 Pro, iOS 26)**
- Apple documentation: "uses Apple Gamut as the color primaries, and an Apple defined Log curve" (https://developer.apple.com/documentation/avfoundation/avcapturecolorspace/applelog2).
- Apple Wide Gamut primaries: R 0.725 0.301, G 0.221 0.814, B 0.068 −0.076, white 0.3127 0.329.
  - These come from the ACES transform `CSC.Apple.AppleLog2_to_ACES.ctl` (added 2026-02-04) and OCIO 2.6.0 `AppleCameras.cpp`, whose comment reads "Apple Wide Gamut primaries from the Apple Log 2 white paper".
  - Both reuse the Apple Log curve constants unchanged.
- The Apple Log 2 white paper itself (https://developer.apple.com/download/all/?q=Apple%20log%202) needs a login, so I did not read it.

**How files signal Apple Log (macOS 26 SDK header `CoreVideo/CVImageBuffer.h`)**
- For Apple Log 2 the header says: "set other color attachments as follows: kCVImageBufferColorPrimariesKey = undefined or absent, kCVImageBufferTransferFunctionKey = undefined or absent, kCVImageBufferYCbCrMatrixKey = kCVImageBufferYCbCrMatrix_ITU_R_2020".
- Identifier strings, printed locally: `com.apple.rec2020.apple-log` and `com.apple.apple-wide-gamut.apple-log` (under key `LogTransferFunction`).

**Status**: Apple Log VERIFIED (mirror). Apple Log 2 primaries UNVERIFIED in Apple's own paper (VERIFIED only in Academy/OCIO sources). Signalling VERIFIED from Apple's SDK header.

## 6. DaVinci Wide Gamut and DaVinci Intermediate

**Source**: Blackmagic, "DaVinci Resolve 17 Wide Gamut Intermediate", August 2021, v1.1. Revision notes: v1.0.1 "Fixed incorrect matrix values"; v1.1 "Fixed incorrect green x chromaticity". https://documents.blackmagicdesign.com/InformationNotes/DaVinci_Resolve_17_Wide_Gamut_Intermediate.pdf

```
DWG  red 0.8000 0.3130  green 0.1682 0.9877  blue 0.0790 -0.1155  white 0.3127 0.3290
RGB→XYZ [0.70062239 0.14877482 0.10105872; 0.27411851 0.87363190 -0.14775041; -0.09896291 -0.13789533 1.32591599]
XYZ→RGB [1.51667204 -0.28147805 -0.14696363; -0.46491710 1.25142378 0.17488461; 0.06484905 0.10913934 0.76141462]
DI_A 0.0075  DI_B 7.0  DI_C 0.07329248  DI_M 10.44426855  DI_LIN_CUT 0.00262409  DI_LOG_CUT 0.02740668
V = (log2(L + DI_A) + DI_B)*DI_C   for L > DI_LIN_CUT ;  V = L*DI_M  for L <= DI_LIN_CUT
L = pow(2.0, (V / DI_C) - DI_B)-DI_A  for V > DI_LOG_CUT ;  L = V/DI_M  for V <= DI_LOG_CUT
0.18 → 0.336043 ; 1.0 → 0.513837 ; 10.0 → 0.756599 ; 100.0 → 1.000000 ; -0.01 → -0.104443
```
**Status**: VERIFIED.

## 7. BT.709, BT.1886, sRGB, HLG, PQ

**BT.709-6 (06/2015)**: https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.709-6-201506-I!!PDF-E.pdf
```
V = 1.099 L^0.45 − 0.099   for 1 ≥ L ≥ 0.018
V = 4.500 L                for 0.018 > L ≥ 0
R 0.640 0.330  G 0.300 0.600  B 0.150 0.060  D65 0.3127 0.3290
EY = 0.2126 ER + 0.7152 EG + 0.0722 EB ; ECB = (EB−EY)/1.8556 ; ECR = (ER−EY)/1.5748
10-bit: black 64, nominal peak 940, CB/CR 64–960
```
- Footnote: "the final picture … viewed on a reference monitor having the reference decoding function of Recommendation ITU-R BT.1886".
- ITU-T H.273 gives the exact constants as α = 1.099296826809442…, β = 0.018053968510807….
- **VERIFIED**.

**BT.1886-0 (03/2011)**: https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1886-0-201103-I!!PDF-E.pdf
```
L = a·(max[(V + b), 0])^γ ,  γ = 2.40
a = (LW^(1/γ) − LB^(1/γ))^γ ,  b = LB^(1/γ) / (LW^(1/γ) − LB^(1/γ)) ,  V = (D−64)/876
```
**VERIFIED**.

**BT.2100-3 (02/2025)**: https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.2100-3-202502-I!!PDF-E.pdf

PQ:
```
FD = 10000·Y ,  Y = ( max[(E'^(1/m2) − c1), 0] / (c2 − c3·E'^(1/m2)) )^(1/m1)
EOTF⁻¹: E' = ((c1 + c2·Y^m1)/(1 + c3·Y^m1))^m2 ,  Y = FD/10000
m1 = 2610/16384 = 0.1593017578125   m2 = 2523/4096×128 = 78.84375
c1 = 3424/4096 = 0.8359375 = c3 − c2 + 1   c2 = 2413/4096×32 = 18.8515625   c3 = 2392/4096×32 = 18.6875
PQ reference OOTF: E' = 1.099(59.5208·E)^0.45 − 0.099 (E > 0.0003024), 267.84·E otherwise; FD = 100·E'^2.4
```

HLG:
```
E' = √(3E)                 0 ≤ E ≤ 1/12
E' = a·ln(12E − b) + c     1/12 < E ≤ 1
a = 0.17883277, b = 1 − 4a = 0.28466892, c = 0.5 − a·ln(4a) = 0.55991073
OOTF: FD = α·YS^(γ−1)·E ,  YS = 0.2627RS + 0.6780GS + 0.0593BS ,  γ = 1.2 at 1000 cd/m²
γ = 1.2 + 0.42·log10(LW/1000)   (outside 400–2000 cd/m²: γ = 1.2·κ^log2(LW/1000), κ = 1.111)
EOTF: FD = OOTF[OETF⁻¹[max(0, (1−β)E' + β)]] ,  β = √(3(LB/LW)^(1/γ))
OETF⁻¹: x²/3 (0 ≤ x ≤ ½) ; (exp((x−c)/a) + b)/12 (½ < x ≤ 1)
```
BT.2100 primaries are the same as BT.2020. **VERIFIED**.

**sRGB**
- IEC 61966-2-1 is paywalled. ITU-T H.273 V4 (07/2024, https://www.itu.int/rec/T-REC-H.273-202407-I), TransferCharacteristics 13, with MatrixCoefficients 0:
```
V = α·Lc^(1/2.4) − (α − 1)  for 1 > Lc ≥ β ;  V = 12.92·Lc  for β > Lc ≥ 0
```
  - H.273 defines α and β as the constants that give "continuity of both value and slope".
- The usual IEC constants (1.055, 0.0031308, 0.04045) are **UNVERIFIED** against IEC itself.
  - They agree with Apple's shipped `Display P3.icc` parametric curve: g 2.399994, a 0.947861, b 0.052139, c 0.077393, d 0.040451, in s15.16 fixed point.
- The 1996 proposal (https://www.w3.org/Graphics/Color/sRGB.html) gives 80 cd/m² and "the equivalent gamma value of 2.2", but its equations are images only.

**H.273 code points used in item 8**: primaries 1 = BT.709, 2 = unspecified, 9 = BT.2020; transfer 1 = BT.709, 2 = unspecified, 13 = sRGB, 16 = PQ, 18 = HLG; matrix 1 = BT.709 (KR 0.2126, KB 0.0722), 9 = BT.2020 non-constant luminance.

## 8. Output tagging for web delivery

**What Apple documents (VERIFIED)**
- TN2227 (2011-11-02, https://developer.apple.com/library/archive/technotes/tn2227/_index.html):
  - "1-1-1 | HD (Rec. 709)".
  - "Media without a 'nclc' tag will be color managed by QuickTime X as if it were created in the SMPTE-C color space."
- Neither TN2227 nor "Tagging media with video color information" (https://developer.apple.com/documentation/avfoundation/tagging-media-with-video-color-information) gives a gamma value for decoding Rec.709 video.

**What macOS actually does (MEASURED)**
- Asking CoreVideo for the 1-1-1 colour space (`CVImageBufferCreateColorSpaceFromAttachments`) returns `kCGColorSpaceCoreMedia709`. Its ICC curve is a pure power function with gamma **1.9609375**: code 0.18 decodes to 0.03464, which is 0.18^1.961.
- The same call with no transfer key returns NULL.
- AVPlayer on ProRes and H.264 test files tagged **1-2-1** attaches `TransferFunction = ITU_R_709_2` and `kCGColorSpaceCoreMedia709`, exactly as for 1-1-1. Decoding to linear through AVAssetReader also gives identical values: code 0.50 → 0.259 for both.
  - **So on macOS 26.4.1, tagging 1-2-1 does not avoid the 1.961 decode.**
- A fully untagged file (2-2-2) at 256x64 is given SMPTE_C primaries, which matches TN2227. (Added 2026-10-11: at 720x480 and 1920x1080 an untagged H.264 file is given BT.709 primaries instead, so the default depends on frame size; see `bench/RESULTS.md`.)
- Two other "709" definitions exist on the same system:
  - `/System/Library/ColorSync/Profiles/ITU-709.icc` ("Rec. ITU-R BT.709-5") uses the exact inverse of the BT.709 encoding curve (parametric g 2.222229, a 0.909912, b 0.090088, c 0.222229, d 0.080994).
  - The named `CGColorSpace.itur_709` converts code 0.18 → 0.01632, i.e. 0.18^2.4, matching BT.1886 with zero black.
- The test scripts are kept in bench/cvtest/.

**DaVinci Resolve "Rec.709-A"**
- Colorist Guide 20 (https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Colorist-Guide.pdf, p. 455-456):
  - "change the Gamma Tag to Rec.709-A to leave the transfer function metadata in the video file unspecified."
  - As an output colour space it will "bake the Rec.709-A gamma into your video. This is not recommended for professional broadcast and projection deliverables".
- Reference Manual 20 (https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_20_Reference_Manual.pdf, p. 108):
  - "Automatically Tag Rec.709 Scene Clips as Rec.709-A … useful if your final QuickTime video does not match what you see in the Resolve viewers (gamma shift), and you wish to export for the web rather than broadcast."
  - "Rec.709 (Scene)" bakes the OOTF into the pixels (Colorist Guide p. 172 and Lesson 10).
- The Rec.709-A curve is not published.
- A Blackmagic forum post claims the Rec.709-A tag actually writes "Rec.709" (https://forum.blackmagicdesign.com/viewtopic.php?f=21&t=227750). That page returned 403, so this is **UNVERIFIED**.

**Platform documentation (VERIFIED)**
- YouTube (https://support.google.com/youtube/answer/1722171):
  - SDR transfer, primaries and matrix all "BT.709 (H.273 value 1)".
  - Unspecified transfer: "Assumes BT.709 TRC."
  - "YouTube converts full color range to limited color range."
- YouTube HDR (https://support.google.com/youtube/answer/7126552):
  - PQ or HLG, Rec.2020, 10/12-bit.
  - PQ files should carry ST 2086 mastering data and MaxCLL/MaxFALL; if missing, YouTube uses Sony BVM-X300 values.
  - An optional `.cube` 3D LUT can be supplied as a hint for SDR down-conversion.
- Vimeo (https://help.vimeo.com/hc/en-us/articles/12425821202961, https://help.vimeo.com/hc/en-us/articles/12426043233169):
  - Tags marked "Unknown" "may cause unexpected color variation".
  - Recommends BT.709 or BT.2020 primaries and matrix.
  - HDR is PQ or HLG only; PQ needs ST 2086 and CEA 861.3 metadata.
- Instagram/Meta: I found no official SDR tagging spec (**UNVERIFIED**). Meta's engineering blog (https://engineering.fb.com/2023/07/17/video-engineering/hdr-video-reels-meta/) says HLG and PQ Reels uploads are supported, and SDR is made with a tuned Hable operator to BT.709.

**What this supports**
- Tagging 1-1-1, limited range, is the only practice that YouTube and Vimeo document.
- The lighter look on a Mac comes from Apple decoding with gamma 1.961. On macOS 26.4.1 a 1-2-1 tag does not change that (measured).
- I found no 2025-2026 primary-source recommendation beyond the pages above.

## 9. Display transforms and tone mapping

**Resolve "DaVinci" tone and gamut mapping**
- Resolve 20 Reference Manual p. 3470 describes the options in words only:
  - "DaVinci: … smooth luminance roll-off in the shadows and highlights, and controlled desaturation".
  - "Simple" maps "between approximately 5500 nits and 100 nits".
  - Saturation Mapping has Knee and Max controls.
- No formula appears in the manual, the Colorist Guide or the DWG paper. I treat it as **not published** (UNVERIFIED that no publication exists anywhere).

**ACES 2.0**
- Releases tagged `v2.0.0+2024.09.05` and `v2.0.0+2025.04.04` (https://github.com/aces-aswf/aces). The algorithm documentation is at https://docs.acescentral.com/.
- Licence: **Apache-2.0** in the aces, aces-core, aces-output and aces-input-and-colorspaces repos.
- OCIO support:
  - 2.4.0 added the ACES 2.0 output transforms, labelled "PREVIEW RELEASE".
  - 2.5 notes: "The ACES 2.0 library support was finalized in the OCIO 2.4.2 release". ACES 2.0 built-in configs arrived in 2.5.0.
  - 2.6.0 ships ACES 2.1 configs: "the Output Transforms are not changed in this ACES release", and its transform IDs are "preliminary" until ACES 2.1 is officially released.
- **MEASURED**: OCIO's generated Metal shader for the ACES 2.0 output needs two 1D textures (`ocio_reach_m_table_0`, `ocio_gamut_cusp_table_0`).

**Open alternatives**
- **OpenDRT** (https://github.com/jedypod/open-display-transform): GPL-3.0, v1.1.0 released 2026-01-22, Nuke and Resolve DCTL. GPL matters if the editor is closed source.
- **AgX** (https://github.com/sobotka/AgX, https://github.com/EaryChow/AgX): GitHub reports no licence file for either repo, which means no licence has been granted. Eary's README says it was "merged to Blender 4.0 main"; whether Blender's copy carries a separate licence is UNVERIFIED.
- **three.js AgX approximation**: MIT.
- **Khronos PBR Neutral** (https://github.com/KhronosGroup/ToneMapping): Apache-2.0, designed for 3D graphics.

## 10. OpenColorIO 2.x

**Version and licence**: 2.6.0, GitHub release 2026-10-01, the base for VFX Reference Platform CY2027. The previous release is 2.5.2 (2026-05-13). Licence BSD-3-Clause.

**Built-in configs and defaults**
- `ocio://default` = `ocio://cg-config-latest` = `cg-config-v5.0.0_aces-v2.1_ocio-v2.6`.
- `ocio://studio-config-latest` = `studio-config-v5.0.0_aces-v2.1_ocio-v2.6`.
- Older built-ins are kept: cg/studio v1.0.0 (OCIO 2.1), v2.1.0 (2.3), v2.2.0 (2.4) and v4.0.0 (2.5).

**Camera colour spaces in studio-config v5.0.0**
- Apple: Apple Log, Apple Log 2, Linear Apple Wide Gamut
- ARRI: LogC3 (EI800), LogC4
- Blackmagic: BMDFilm WideGamut Gen5, DaVinci Intermediate WideGamut, Linear DaVinci WideGamut
- Canon: CanonLog2 / CanonLog3 CinemaGamut D55
- DJI: D-Log D-Gamut, Linear D-Gamut
- Panasonic: V-Log V-Gamut
- RED: Log3G10 REDWideGamutRGB
- Sony: S-Log3 S-Gamut3, S-Log3 S-Gamut3.Cine, plus Venice variants (now family "Input/Sony/Legacy")
- Curve-only entries for each log.

**When each was added**
- DWG/DI and BMDFilm Gen5: since v1.0.0 (OCIO 2.1)
- Apple Log: v2.2.0 (OCIO 2.4)
- D-Log: v4.0.0 (OCIO 2.5)
- Apple Log 2: v5.0.0 (OCIO 2.6)

**Not included anywhere**: S-Log2/S-Gamut, D-Log M, older Blackmagic generations. The cg-config has no camera spaces at all.

**Camera built-in transforms**: `APPLE_LOG_to_ACES2065-1`, `APPLE_LOG-APPLEWG_to_ACES2065-1`, `SONY_SLOG3-SGAMUT3[.CINE][-VENICE]_to_ACES2065-1`, plus ARRI, Canon, Panasonic and RED. There is no DJI or Blackmagic built-in transform; the config builds those from LogCameraTransform plus a matrix.

**GPU shader languages (2.6.0 enum)**: Cg, GLSL 1.2 / 1.3 / 4.0, GLSL Vulkan 4.6, HLSL SM 5.0, OSL 1, GLSL ES 1.0 / 3.0, **MSL 2.0**. Metal support was added in PR #1520 (Nov 2021) and first shipped in v2.2.0.

**Python wheels**: `opencolorio` 2.6.0 on PyPI has cp310-cp314 `macosx_11_0_arm64` wheels; 2.5.x also has cp39.
- **MEASURED**: the wheel imports on arm64.
- It generated a 17,720-character Metal shader for DaVinci Intermediate WideGamut → Rec.1886 Rec.709, ACES 2.0 SDR 100 nits view.
- In that transform, DI code 0.336043 (18% grey) maps to 0.3831.

## 11. colour-science (Python)

**Release**: 0.4.7 (PyPI 2025-12-06), BSD-3-Clause, Python ≥3.11,<3.15.

**Covered in 0.4.7 (VERIFIED by enumerating the installed package)**
- Transfer functions: S-Log, S-Log2, S-Log3, D-Log, Apple Log Profile, DaVinci Intermediate, Blackmagic Film Generation 5, BT.709, BT.1886 (with `L_B`, `L_W`), sRGB, ST 2084, BT.2100 PQ and HLG (OOTF, gamma override, BT.2100-1/-2 methods), ARIB STD-B67, H.273 sRGB variant.
- Colourspaces: S-Gamut, S-Gamut3, S-Gamut3.Cine, Venice variants, DJI D-Gamut, DaVinci Wide Gamut, Blackmagic Wide Gamut, ITU-R BT.709, ITU-R BT.2020, P3-D65, Display P3, sRGB.
- Apple Log has no separate colourspace entry; use ITU-R BT.2020.

**Missing**: Apple Log 2 / Apple Wide Gamut, D-Log M, and any ACES 2.0 output transform. It has the Hellwig 2022 appearance model and a `colour.io.ocio` wrapper, but no ACES 2.0 display transform.

**Unreleased develop branch (v0.4.8)**: opt-in Array API dispatch for "JAX, PyTorch (including MPS GPU), and CuPy", enabled with `COLOUR_SCIENCE__ARRAY_API=1` and off by default. This is not in 0.4.7.
