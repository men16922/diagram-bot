/* Diagram Bot integration for the pinned draw.io build; upstream stays unmodified. */
App.initPluginCallback();
Draw.loadPlugin(function (ui) {
  const graph = ui.editor.graph;
  Editor.defaultCompressed = false;
  const send = data => parent.postMessage(JSON.stringify(data), location.origin);
  const cells = () => Object.values(graph.getModel().cells);
  const isRelation = cell => cell?.getAttribute && (cell.getAttribute('relationId') != null || cell.getAttribute('relationIndex') != null);
  graph.getSelectionModel().addListener(mxEvent.CHANGE, function () {
    // Multi-selection is used for relation highlighting; only a single cell drives the inspector.
    if (graph.getSelectionCount() > 1) return;
    const cell = graph.getSelectionCell();
    send({event: 'diagramBotSelection', resourceId: cell?.getAttribute('resourceId'), summary: cell?.getAttribute('summary') === '1',
      relationId: cell?.getAttribute('relationId'), relationIndex: cell?.getAttribute('relationIndex'),
      label: cell ? graph.convertValueToString(cell) : null});
  });
  const originalDisconnect = graph.isCellDisconnectable;
  graph.isCellDisconnectable = function (cell, terminal, source) {
    return !isRelation(cell) && originalDisconnect.apply(this, arguments);
  };
  window.addEventListener('message', function (event) {
    if (event.origin !== location.origin || event.source !== parent) return;
    let message;
    try { message = typeof event.data === 'string' ? JSON.parse(event.data) : event.data; }
    catch { return; }
    if (!message || !message.action?.startsWith('diagramBot')) return;
    try {
      if (message.action === 'diagramBotOverview') {
        if (ui.sidebarWindow) ui.sidebarWindow.window.setVisible(false);
        if (ui.formatWindow) ui.formatWindow.window.setVisible(false);
        ui.setFitWindowEnabled(false);
        ui.fitDiagramOrPages();
      } else if (message.action === 'diagramBotDocument') {
        graph.stopEditing(false);
        const xml = ui.getFileData(true, false, false, null, true, false, null, false, null, true);
        send({event: 'diagramBotDocument', requestId: message.requestId, xml});
      } else if (message.action === 'diagramBotSelect') {
        const cell = cells().find(c => c.getAttribute && c.getAttribute('resourceId') === message.resourceId);
        if (!cell) { send({event: 'diagramBotNotVisible'}); return; }
        const related = new Set(message.relations || []);
        const edges = cells().filter(c => c.getAttribute && related.has(c.getAttribute('relationId')));
        graph.setSelectionCells([cell, ...edges]);
        graph.scrollCellToVisible(cell);
        if (message.relations) send({event: 'diagramBotHighlighted', count: edges.length});
      } else if (message.action === 'diagramBotSelectRelation') {
        const cell = cells().find(c => c.getAttribute && ((message.relationId && c.getAttribute('relationId') === message.relationId)
          || (message.relationIndex != null && c.getAttribute('relationIndex') === String(message.relationIndex))));
        if (cell) { graph.setSelectionCell(cell); graph.scrollCellToVisible(cell); }
        else send({event: 'diagramBotNotVisible'});
      } else if (message.action === 'diagramBotNote') {
        const parentCell = graph.getDefaultParent();
        const bounds = graph.getGraphBounds();
        const scale = graph.view.scale;
        const translation = graph.view.translate;
        const note = graph.insertVertex(parentCell, null, '검토 메모\n사용자 주석',
          bounds.x / scale - translation.x + 25, (bounds.y + bounds.height) / scale - translation.y + 30,
          220, 85, 'rounded=1;html=0;whiteSpace=wrap;fillColor=#FFF3CD;strokeColor=#D6B453;fontColor=#6D5620;fontSize=14;');
        graph.setSelectionCell(note);
        graph.scrollCellToVisible(note);
        send({event: 'diagramBotNoteAdded'});
      }
    } catch (error) {
      send({event: 'diagramBotError', requestId: message.requestId, error: String(error.message || error)});
    }
  });
});
