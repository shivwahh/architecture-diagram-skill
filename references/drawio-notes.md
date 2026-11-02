# draw.io XML notes (for hand edits)

Prefer the scripts. When you must edit XML directly, these are the facts that matter.

## Structure

```xml
<mxfile host="app.diagrams.net">
  <diagram id="p1" name="Page name">                <!-- one per page -->
    <mxGraphModel grid="1" gridSize="10" page="1" pageWidth="1600" pageHeight="1000" ...>
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>                    <!-- default layer -->
        ... vertices and edges ...
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

- Older files may store each page compressed (the `<diagram>` holds encoded text). `archdiagram.py decode in out` writes a readable copy; the scripts read either form.
- A parent must appear before its children in the XML. Later cells draw on top of earlier ones.
- Ids must be unique across the page. Keep existing ids when editing; other tools and later edits rely on them.

## Vertices

```xml
<mxCell id="order" value="&lt;b&gt;Order service&lt;/b&gt;" style="rounded=1;whiteSpace=wrap;html=1;..." vertex="1" parent="aks">
  <mxGeometry x="20" y="50" width="230" height="60" as="geometry"/>
</mxCell>
```

- **Coordinates are relative to the parent.** A cell inside a container (`parent="aks"`) is positioned from the container's top-left corner, not the page. Convert by adding or subtracting the parent's absolute position. This is the most common source of misplaced shapes.
- Containers have `container=1` in the style. Use `recursiveResize=0` so resizing doesn't stretch the contents, and `collapsible=0` to hide the fold button.
- With `html=1`, the value is HTML: escape `&`, `<`, `>` and `"` in XML (`&amp;` `&lt;` `&gt;` `&quot;`). A line break is `&lt;br&gt;`.
- Icons are image cells: `shape=image;aspect=fixed;imageAspect=1;image=data:image/svg+xml,BASE64;`. draw.io leaves out `;base64` because `;` separates style keys. Icon badges are children of their component, so they move with it.

## Edges

```xml
<mxCell id="flow-order-db" value="SQL" edge="1" parent="1" source="order" target="orders_db"
        style="edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;">
  <mxGeometry relative="1" as="geometry" x="-0.2">
    <Array as="points"><mxPoint x="700" y="160"/><mxPoint x="700" y="300"/></Array>
  </mxGeometry>
</mxCell>
```

- `exitX/exitY` and `entryX/entryY` fix where the connector leaves and enters (fractions of the shape: 1,0.5 is the middle of the right side). `exitDy`/`entryDy` add a pixel offset.
- With `orthogonalEdgeStyle`, the points are bend positions. The route goes horizontally or vertically from each to the next, so give the actual corners.
- Points are in the edge parent's coordinate system. An edge between shapes in different containers should have `parent` set to their lowest common container (or the layer) with points converted accordingly.
- The `x` attribute on the edge geometry places the label along the path: -1 at the source, 0 in the middle, 1 at the target.
- Async flows use `dashed=1;dashPattern=6 4`; two-way flows add `startArrow=block;startFill=1`.

## Metadata this skill writes

Style keys draw.io ignores but keeps, used for later edits and validation:

- `archRole`: one of component, icon, boundary, flow, legend, legend-item, title, subtitle, layer-label
- `archType`: component type
- `archTech`: the technology as given
- `archIcon`: the icon key
- `archKind`: boundary kind
- `archFlow`: sync or async

Keep these when copying styles, and set them on shapes you add by hand, so `check` can verify icons and `apply-icons --refresh` can upgrade them.
