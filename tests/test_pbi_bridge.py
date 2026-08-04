"""Tests for the TMDL parsing and the measure-reference helpers.

No Azure, no Node.js, no network: these run anywhere, including in the five minutes before a
talk when the conference wifi has given up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foundry_mcp.pbi_bridge import (connect_request, normalise_measure_reference, parse_measures)

SAMPLE = (Path(__file__).resolve().parents[1] / "samples" / "sample-model" /
          "Sales Demo.SemanticModel" / "definition" / "tables" / "Sales.tmdl")


# --- measure references -------------------------------------------------------------------------

@pytest.mark.parametrize("reference,expected", [
    ("Sales[Total Sales]", "Total Sales"),
    ("[Total Sales]", "Total Sales"),
    ("Total Sales", "Total Sales"),
    ("  Measures_[Margin %]  ", "Margin %"),
])
def test_normalise_measure_reference(reference, expected):
    assert normalise_measure_reference(reference) == expected


# --- TMDL parsing -------------------------------------------------------------------------------

def test_parses_inline_measure():
    """The single-line shape: `measure 'X' = EXPR`."""
    tmdl = "table Sales\n\n\tmeasure 'Total Sales' = SUM(Sales[Amount])\n\t\tformatString: #,0.00\n"
    measures = parse_measures(tmdl)
    assert measures["Total Sales"]["expression"] == "SUM(Sales[Amount])"
    assert measures["Total Sales"]["table"] == "Sales"


def test_parses_fenced_block_measure():
    """The multi-line shape, where the expression sits between ``` fences."""
    tmdl = (
        "table Sales\n\n"
        "\tmeasure 'Total Cost' =\n"
        "\t\t\t```\n"
        "\t\t\tSUMX(\n"
        "\t\t\t    Sales,\n"
        "\t\t\t    Sales[Cost]\n"
        "\t\t\t)\n"
        "\t\t\t```\n"
        "\t\tformatString: #,0.00\n"
    )
    expression = parse_measures(tmdl)["Total Cost"]["expression"]
    assert expression.startswith("SUMX(")
    assert "Sales[Cost]" in expression


def test_attributes_each_measure_to_its_own_table():
    """Measures are nested under a table; the parser must not attribute them to the first one."""
    tmdl = (
        "table Sales\n\tmeasure 'Total Sales' = SUM(Sales[Amount])\n\n"
        "table Customer\n\tmeasure 'Customer Count' = DISTINCTCOUNT(Customer[CustomerId])\n"
    )
    measures = parse_measures(tmdl)
    assert measures["Total Sales"]["table"] == "Sales"
    assert measures["Customer Count"]["table"] == "Customer"


def test_ignores_documentation_comments():
    """/// lines describe the next object; they are not an expression."""
    tmdl = "table Sales\n\n\t/// Revenue for the period.\n\tmeasure Revenue = SUM(Sales[Amount])\n"
    assert parse_measures(tmdl)["Revenue"]["expression"] == "SUM(Sales[Amount])"


def test_empty_model_yields_no_measures():
    assert parse_measures("") == {}
    assert parse_measures("table Empty\n\tcolumn Id\n\t\tdataType: int64\n") == {}


# --- against the sample model shipped with the repo ---------------------------------------------

def test_parses_the_sample_model():
    """The sample is the demo: if this breaks, the talk breaks."""
    measures = parse_measures(SAMPLE.read_text(encoding="utf-8"))
    assert set(measures) == {"Total Sales", "Total Cost", "Profit", "Margin %", "Revenue"}
    assert all(m["table"] == "Sales" for m in measures.values())
    assert measures["Total Cost"]["expression"].startswith("SUMX(")     # block form
    assert measures["Total Sales"]["expression"] == "SUM(Sales[Amount])"  # inline form


def test_sample_model_contains_the_planted_duplicate():
    """'Revenue' and 'Total Sales' are the same calculation: the demo's needle in the haystack."""
    measures = parse_measures(SAMPLE.read_text(encoding="utf-8"))
    assert measures["Revenue"]["expression"] == measures["Total Sales"]["expression"]


# --- connection requests ------------------------------------------------------------------------

def test_connect_request_fabric():
    request = connect_request(workspace="My Workspace", model="Sales")
    assert request == {"operation": "ConnectFabric", "workspaceName": "My Workspace",
                       "semanticModelName": "Sales"}


def test_connect_request_folder():
    assert connect_request(folder="C:/models/Sales")["operation"] == "ConnectFolder"


def test_connect_request_needs_enough_information():
    with pytest.raises(ValueError):
        connect_request(workspace="My Workspace")   # model missing
    with pytest.raises(ValueError):
        connect_request()
