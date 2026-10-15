"""Edit operations (docs/design/06-editing.md#edit-operations): the functions `lungecuta op` and
the editor both call, so an agent never ripples a timeline by editing every later
layer's JSON by hand.

Each operation changes the fields After Effects' model says it changes (D11,
03-project-format.md#layer-timing): a move changes `start` and carries the keyframes; a
trim changes `in` or `out`; a slip changes `source_start`; slide, roll and ripple follow
from those. A value keeps the unit it was written in, so a beat-anchored edit stays
beat-anchored: the new time is computed in ticks and written back in the field's own
unit, which must hold it exactly (or, for beats, to the nearest tick). The comp file is
written only if it is still the one read, and each change is reported in editing terms.
"""
from __future__ import annotations

from .comps import new_comp
from .edit import Edit
from .grading import look, set_grade, unlook
from .properties import KEYED, LAYER_OF, key, key_position, move_key, remap, set_masks, set_value, speed, unkey
from .sounds import EVENTS, ROTATION, duck, sfx, sound
from .styles import style, transition, unstyle, untransition
from .timing import cut, graphic, move, place, ripple_delete, roll, slide, slip, snap, split, trim


# Every edit operation by the name `lungecuta op` and the editor give it: the one table both use.
OPERATIONS = {
    "move": move, "trim": trim, "slip": slip, "slide": slide, "roll": roll, "ripple-delete": ripple_delete,
    "split": split, "snap": snap, "place": place, "style": style, "unstyle": unstyle, "key": key, "unkey": unkey,
    "set": set_value, "remap": remap, "grade": set_grade, "masks": set_masks, "transition": transition,
    "untransition": untransition, "look": look, "unlook": unlook, "sfx": sfx, "move-key": move_key,
    "graphic": graphic, "speed": speed, "sound": sound, "duck": duck,
    "cut": cut,
}

__all__ = ['EVENTS', 'Edit', 'cut', 'duck', 'KEYED', 'LAYER_OF', 'ROTATION', 'graphic', 'key', 'key_position', 'look', 'move', 'move_key', 'new_comp', 'place', 'remap', 'ripple_delete', 'roll', 'set_grade', 'set_masks', 'set_value', 'sfx', 'slide', 'slip', 'snap', 'sound', 'speed', 'split', 'style', 'transition', 'trim', 'unkey', 'unlook', 'unstyle', 'untransition', 'OPERATIONS']
