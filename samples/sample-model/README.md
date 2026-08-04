# Sample semantic model

A tiny PBIP semantic model so you can try the tools without opening Power BI.

Two tables (`Sales`, `Customer`), one relationship, six measures. It contains, on purpose:

- both TMDL measure shapes — inline (`Total Sales`) and fenced block (`Total Cost`);
- a percentage with no percentage format (`Margin %`);
- a duplicate calculation under a second name (`Revenue` is `Total Sales` again).

Those last two are the sort of thing worth catching in a real model, and they give the demo
something to actually find.

Use it either way:

```text
inspect_model folder="samples/sample-model"    # needs Node.js (launches the official MCP server)
```

```python
# no Node.js, no Power BI: parse the TMDL directly
from pathlib import Path
from foundry_mcp.pbi_bridge import parse_measures

tmdl = Path("samples/sample-model/Sales Demo.SemanticModel/definition/tables/Sales.tmdl").read_text()
print(parse_measures(tmdl))
```
