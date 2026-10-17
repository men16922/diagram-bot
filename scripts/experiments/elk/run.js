const ELK = require('elkjs/lib/elk.bundled.js');
const graphs = require(process.argv[2]);
const elk = new ELK();
function boxes(n, ox, oy, out, parent) {
  for (const c of n.children || []) { const x = ox + c.x, y = oy + c.y; out.push({id: c.id, x, y, w: c.width, h: c.height, parent, leaf: !c.children || !c.children.length}); boxes(c, x, y, out, c.id); }
  return out;
}
(async () => {
  for (const [name, g] of Object.entries(graphs)) {
    const t = process.hrtime.bigint();
    const a = await elk.layout(JSON.parse(JSON.stringify(g)));
    const ms = Number(process.hrtime.bigint() - t) / 1e6;
    const b = await elk.layout(JSON.parse(JSON.stringify(g)));
    const same = JSON.stringify(a) === JSON.stringify(b);
    const bx = boxes(a, 0, 0, [], 'root');
    let overlaps = 0, outside = 0;
    const byId = Object.fromEntries(bx.map(x => [x.id, x]));
    for (const p of bx) { const q = byId[p.parent]; if (q && (p.x < q.x || p.y < q.y || p.x + p.w > q.x + q.w || p.y + p.h > q.y + q.h)) outside++; }
    for (let i = 0; i < bx.length; i++) for (let j = i + 1; j < bx.length; j++) { const p = bx[i], q = bx[j]; if (p.parent !== q.parent) continue;
      if (p.x < q.x + q.w && q.x < p.x + p.w && p.y < q.y + q.h && q.y < p.y + p.h) overlaps++; }
    // Edge segments crossing a leaf node that is not an endpoint.
    let crossings = 0;
    const edges = []; (function collect(n, ox, oy) { for (const e of n.edges || []) edges.push([e, ox, oy]); for (const c of n.children || []) collect(c, ox + c.x, oy + c.y); })(a, 0, 0);
    for (const [e, ox, oy] of edges) for (const s of e.sections || []) {
      const pts = [s.startPoint, ...(s.bendPoints || []), s.endPoint].map(p => ({x: p.x + ox, y: p.y + oy}));
      for (let i = 0; i + 1 < pts.length; i++) { const [p, q] = [pts[i], pts[i + 1]];
        for (const n of bx) { if (!n.leaf || e.sources.includes(n.id) || e.targets.includes(n.id)) continue;
          const minx = Math.min(p.x, q.x), maxx = Math.max(p.x, q.x), miny = Math.min(p.y, q.y), maxy = Math.max(p.y, q.y);
          if (maxx > n.x + 1 && minx < n.x + n.w - 1 && maxy > n.y + 1 && miny < n.y + n.h - 1) crossings++; } } }
    const azs = bx.filter(x => x.id.includes('/ap-northeast'));
    console.log(JSON.stringify({graph: name, ms: Math.round(ms), deterministic: same, nodes: bx.length, width: Math.round(a.width), height: Math.round(a.height),
      sibling_overlaps: overlaps, outside_parent: outside, edge_node_crossings: crossings,
      az_columns_side_by_side: azs.length > 1 ? new Set(azs.map(z => Math.round(z.y))).size === 1 : null}));
  }
})();
