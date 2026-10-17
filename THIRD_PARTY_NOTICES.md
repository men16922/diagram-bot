# Third-party notices

Diagram Bot uses the official draw.io web editor locally. The upstream distribution is downloaded by `diagram-bot setup-editor` and is not vendored in this repository or the Python wheel.

| Component | Source / version | Terms |
| --- | --- | --- |
| draw.io editor | [jgraph/drawio v32.4.1](https://github.com/jgraph/drawio/tree/v32.4.1) | [Upstream Apache 2.0 license](https://github.com/jgraph/drawio/blob/v32.4.1/LICENSE) |
| Shapes and stencil libraries, including AWS4 | Same official distribution | Separate [shapes/LICENSE](https://github.com/jgraph/drawio/blob/v32.4.1/src/main/webapp/shapes/LICENSE) and [stencils/LICENSE](https://github.com/jgraph/drawio/blob/v32.4.1/src/main/webapp/stencils/LICENSE) apply |
| Other bundled images, templates and libraries | Same official distribution | Their notices and license files remain in the installed assets |

Official archive: https://github.com/jgraph/drawio/releases/download/v32.4.1/draw.war

SHA-256: `b83663313ccdecef6581476a7eaa96750bccf1bfdc3768981eb5d95e94be7820`

The shape/stencil license at this version restricts incorporation of these software assets into Atlassian products and the Atlassian marketplace/plugin ecosystem without permission; it expressly excludes end-user diagram outputs such as images and documents from that restriction. Diagram Bot is a standalone local application. Consult the upstream terms before repackaging these assets for another ecosystem.

`static/editor-bridge.js` is Diagram Bot's integration code. The local server appends it to the served upstream script to connect selection, document save and notes to the workspace. The downloaded source files and bundled notices are retained on disk. The adapter is tested against the pinned version; an editor upgrade requires another browser acceptance run.

AWS resource names and icons identify AWS services. Diagram Bot is not an AWS or draw.io product and does not claim their endorsement.
