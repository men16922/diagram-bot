# Layout Engine Evaluation (P2)

Date: 2026-10-17. Decision: keep a deterministic Python layered layout (`diagram-bot-layered/2`);
ELK is not adopted. Every generated document records the engine in the `scope` cell's
`layout` attribute.

## ELK layered experiment

Reproduce: `scripts/experiments/elk/export.py out.json` then `node scripts/experiments/elk/run.js out.json`
with `elkjs@0.9.3` installed in a scratch directory (not vendored). Input: detail view of the
synthetic sample (`vpc-demo`, 17 nodes) and the 100-resource fixture (`vpc-scale-0`, 52 nodes),
VPC > AZ > subnet nesting, `INCLUDE_CHILDREN`, orthogonal routing, direction DOWN.

| Check | Sample | 100-resource |
| --- | --- | --- |
| Layout time (Node 22, warm) | 109 ms | 79 ms |
| Same positions on rerun | yes | yes |
| Sibling overlaps / child outside parent | 0 / 0 | 0 / 0 |
| Edge segments through a non-endpoint node | 0 | 0 |
| AZ columns side by side | no (stacked by layer) | no |
| Canvas width | 1105 px | 6524 px |

Findings that ruled it out for this scope:

- AZ groups are placed per edge layer, not as side-by-side columns, losing the AZ/subnet reading
  order the plan requires. Forcing columns needs partitioning options per group and custom
  post-processing, i.e. a second layout model to maintain.
- Empty subnets collapse to 0×0 unless minimum sizes are set per group.
- Server/CLI documents would need a JavaScript runtime (Node or a browser Worker), against the
  decision not to add Node to the Python/CLI path; browser-only use would make CLI and app
  layouts differ. Asset: 1.6 MB `elk.bundled.js`, EPL-2.0.

## Chosen layout

`src/diagram_bot/layout.py` produces geometry only; `drawio.py` serializes it.

- Bands inside a VPC: load balancers (VPC level, once even when multi-subnet), target groups
  (logical, never inside a subnet), an edge channel, AZ columns with subnets and instances,
  then VPC resources whose subnet is unconfirmed. Unplaced/reference resources go to a separate
  container outside the VPC.
- Ordering is fixed: LB by name; target groups by LB barycenter; instances by target-group
  barycenter, then name/ID. Same input and options produce identical coordinates.
- Routes are explicit: target-group edges use the channel, the gutter left of the target's AZ
  column and the lane above the target's row; SG/ENI/EBS reference edges leave from the icon's
  left side and use the same gutters. Parallel registrations of one instance on several ports
  are offset and labelled with the port.
- Labels are truncated to three wrapped lines (full name in the tooltip and inspector).

Verified by `tests/test_drawio.py`: no sibling overlap (icons plus estimated label boxes), no
child outside its container, no routed segment through another icon or its label, identical
output on rerun, for all views of the sample and both VPCs of the 100-resource fixture.
100-resource fixture, all pages (overview + 2 detail): about 0.02 s on the development container
(test limit 5 s). This is a synthetic measurement, not a large-environment support claim.
Rendered draw.io output of the sample was reviewed visually; the 100-resource view is
structurally correct but dense (≈170 drawn relations), so readability at that size is limited.
