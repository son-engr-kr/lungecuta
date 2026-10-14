"""What the person has on screen in the editor, as lungecuta changes prints it (src/lungecuta/view.py)."""
import json

import pytest
from click.testing import CliRunner

from lungecuta.cli import main
from lungecuta.media import Project
from lungecuta.view import VIEW, read_view, remove_view, write_view


def changes(project):
    result = CliRunner().invoke(main, ["changes", "--project", str(project)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_the_playhead_selection_and_marks_are_placed_in_the_comp(cut_project):
    assert changes(cut_project)["editor"] is None
    write_view(cut_project, "main", 68, "shot2", 30, 90)
    view = read_view(Project.load(cut_project))
    # beats every 0.5 s from 0.25 s at 30000/1001: frame 68 is 2.268933 s, beat 4 plus 0.037867 of the next;
    # the layers are those whose picture is on screen
    assert view["playhead"] == {"frame": 68, "seconds": 2.268933, "beat": 4.0379, "layers": ["shot2"]}
    assert view["selected"] == {"layer": "shot2", "in_comp": True}
    assert view["marked"]["in"]["frame"] == 30 and view["marked"]["out"]["frame"] == 90
    assert (view["marked"]["from"], view["marked"]["to"]) == ("f:30", "f:91")  # as lungecuta sheet --from --to take them
    assert changes(cut_project)["editor"] == view
    remove_view(cut_project)
    assert not (cut_project / VIEW).exists() and changes(cut_project)["editor"] is None


def test_one_mark_reaches_to_the_comp_s_edge(cut_project):
    write_view(cut_project, "main", 0, None, None, 20)
    view = read_view(Project.load(cut_project))
    assert "selected" not in view and view["marked"]["in"] is None
    assert (view["marked"]["from"], view["marked"]["to"]) == ("f:0", "f:21")


def test_a_view_from_before_an_edit_says_what_no_longer_holds(cut_project):
    write_view(cut_project, "main", 500, "gone", None, None)
    view = read_view(Project.load(cut_project))
    assert view["playhead"] == {"frame": 500, "past_end": view["playhead"]["past_end"]} and view["playhead"]["past_end"] < 500
    assert view["selected"] == {"layer": "gone", "in_comp": False}


def test_marks_out_of_order_are_refused(cut_project):
    with pytest.raises(AssertionError, match="is after out"):
        write_view(cut_project, "main", 0, None, 40, 20)
    with pytest.raises(AssertionError, match="no comp"):
        write_view(cut_project, "other", 0, None, None, None)
