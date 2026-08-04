"""Generate the README architecture diagram, in light and dark.

One generator, two palettes: the geometry is identical in both, so they cannot drift apart the
way two hand-drawn files would. Palettes follow GitHub's own tokens, so the diagram reads as part
of the page rather than a screenshot pasted into it.

    python docs/img/generate.py
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent

LIGHT = dict(
    bg="#ffffff", fg="#1f2328", muted="#59636e", border="#d1d9e0",
    surface="#f6f8fa", accent="#bc4c00", accent_bg="#fff1e5", accent_border="#ffb77c",
    ms="#0969da", ms_bg="#ddf4ff", ms_border="#54aeff",
)
DARK = dict(
    bg="#0d1117", fg="#e6edf3", muted="#9198a1", border="#3d444d",
    surface="#161b22", accent="#f0883e", accent_bg="#2a1a10", accent_border="#a04b12",
    ms="#4493f8", ms_bg="#101d2f", ms_border="#1f4d8f",
)

W, H = 960, 500
FONT = "system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"


def box(x, y, w, h, fill, stroke, rx=10, width=1.5):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{width}"/>')


def text(x, y, s, *, fill, size=14, weight="400", anchor="start", font=FONT):
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-family="{font}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}">{s}</text>')


def arrow(x1, y1, x2, y2, colour, curved=False):
    if curved:
        mx = (x1 + x2) / 2
        d = f"M {x1} {y1} C {mx} {y1}, {mx} {y2}, {x2 - 9} {y2}"
    else:
        d = f"M {x1} {y1} L {x2 - 9} {y2}"
    return (f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="1.8" '
            f'marker-end="url(#head)"/>')


def circle_num(cx, cy, n, *, fill, fg):
    return (f'<circle cx="{cx}" cy="{cy}" r="10" fill="{fill}"/>'
            + text(cx, cy + 4, n, fill=fg, size=12, weight="700", anchor="middle"))


def build(p: dict) -> str:
    label = ("Architecture: VS Code Copilot Chat talks over MCP to this server, which bridges to "
             "Microsoft Foundry, Microsoft's Power BI modeling MCP server, and the Fabric REST API.")
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
         f'height="{H}" role="img" aria-label="{label}">',
         f'<defs><marker id="head" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" '
         f'markerHeight="7" orient="auto-start-reverse">'
         f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{p["muted"]}"/></marker></defs>',
         f'<rect width="{W}" height="{H}" fill="{p["bg"]}"/>']

    # -- left: where the user already is ---------------------------------------------------
    o.append(box(20, 208, 196, 84, p["surface"], p["border"]))
    o.append(text(118, 240, "VS Code", fill=p["fg"], size=15, weight="600", anchor="middle"))
    o.append(text(118, 261, "GitHub Copilot Chat", fill=p["fg"], size=15, weight="600",
                  anchor="middle"))
    o.append(text(118, 281, "the user is already here", fill=p["muted"], size=11.5,
                  anchor="middle"))

    o.append(arrow(216, 250, 290, 250, p["muted"]))
    o.append(text(253, 242, "MCP", fill=p["muted"], size=11, anchor="middle", font=MONO))
    o.append(text(253, 268, "stdio", fill=p["muted"], size=11, anchor="middle", font=MONO))

    # -- centre: this repo -----------------------------------------------------------------
    o.append(box(290, 118, 250, 264, p["accent_bg"], p["accent_border"], rx=12, width=2))
    o.append(text(415, 150, "foundry-copilot-mcp", fill=p["accent"], size=16, weight="700",
                  anchor="middle", font=MONO))
    o.append(text(415, 170, "this repo &#183; ~400 lines", fill=p["muted"], size=11.5,
                  anchor="middle"))
    o.append(f'<line x1="312" y1="184" x2="518" y2="184" stroke="{p["accent_border"]}" '
             f'stroke-width="1"/>')

    for y, n, name in [(214, "1", "Foundry bridge"), (274, "2", "MCP client"),
                       (334, "3", "Fabric REST")]:
        # the digit takes the page background colour: maximum contrast on the accent circle
        o.append(circle_num(332, y - 4, n, fill=p["accent"], fg=p["bg"]))
        o.append(text(352, y, name, fill=p["fg"], size=13.5, weight="600"))

    # -- right: what we talk to ------------------------------------------------------------
    o.append(box(620, 60, 320, 96, p["ms_bg"], p["ms_border"]))
    o.append(text(636, 88, "Microsoft Foundry", fill=p["ms"], size=15, weight="700"))
    o.append(text(636, 110, "your agent &#8212; instructions, tools and", fill=p["fg"], size=12))
    o.append(text(636, 128, "model deployment all stay server-side", fill=p["fg"], size=12))
    o.append(text(636, 146, "get_openai_client&#40;agent_name=&#8230;&#41;", fill=p["muted"],
                  size=11, font=MONO))

    o.append(box(620, 190, 320, 120, p["ms_bg"], p["ms_border"]))
    o.append(text(636, 218, "Power BI modeling MCP", fill=p["ms"], size=15, weight="700"))
    o.append(text(636, 240, "Microsoft&#39;s own server, launched as a", fill=p["fg"], size=12))
    o.append(text(636, 258, "child process &#8212; read-only by default", fill=p["fg"], size=12))
    o.append(f'<line x1="636" y1="272" x2="924" y2="272" stroke="{p["ms_border"]}" '
             f'stroke-width="1" stroke-dasharray="3 3"/>')
    o.append(text(636, 292, "&#8594;  Fabric / Power BI  &#40;XMLA, as the user&#41;",
                  fill=p["muted"], size=12))

    o.append(box(620, 344, 320, 96, p["ms_bg"], p["ms_border"]))
    o.append(text(636, 372, "Fabric REST API", fill=p["ms"], size=15, weight="700"))
    o.append(text(636, 394, "turns the GUIDs in a Fabric URL into", fill=p["fg"], size=12))
    o.append(text(636, 412, "the display names the tools need", fill=p["fg"], size=12))
    o.append(text(636, 430, "GET /v1/workspaces/&#123;id&#125;", fill=p["muted"], size=11,
                  font=MONO))

    o.append(arrow(540, 210, 620, 108, p["muted"], curved=True))
    o.append(arrow(540, 270, 620, 250, p["muted"], curved=True))
    o.append(arrow(540, 330, 620, 392, p["muted"], curved=True))

    # -- footer ----------------------------------------------------------------------------
    o.append(f'<line x1="20" y1="462" x2="940" y2="462" stroke="{p["border"]}" '
             f'stroke-width="1"/>')
    o.append(text(20, 484, "Every call runs as the signed-in user, never a service principal "
                           "&#8212; so row-level security applies.", fill=p["muted"], size=12.5))
    o.append("</svg>")
    return "\n".join(o)


if __name__ == "__main__":
    (OUT / "architecture.svg").write_text(build(LIGHT), encoding="utf-8", newline="")
    (OUT / "architecture-dark.svg").write_text(build(DARK), encoding="utf-8", newline="")
    print(f"written: {OUT / 'architecture.svg'}")
    print(f"         {OUT / 'architecture-dark.svg'}")
