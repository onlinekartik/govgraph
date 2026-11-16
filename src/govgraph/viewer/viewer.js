"use strict";

// Draws a govgraph JSON export as a force-directed SVG graph. No dependencies.
// Every string from the export is written with textContent or setAttribute, never as
// markup, so metadata cannot inject script or HTML into the page.
(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const PALETTE = [
    "#1f77b4", "#d95f02", "#2a9d5c", "#c2185b", "#7b61c4", "#8c6d31",
    "#0097a7", "#e0a100", "#5d6d7e", "#b23b3b", "#3f8f29", "#a04fbf",
  ];
  const NODE_RADIUS = 9;
  const REST_LENGTH = 150;

  const canvas = document.getElementById("canvas");
  const viewport = document.getElementById("viewport");
  const edgeLayer = document.getElementById("edges");
  const nodeLayer = document.getElementById("nodes");
  const legend = document.getElementById("legend");
  const details = document.getElementById("details");
  const summary = document.getElementById("summary");
  const warning = document.getElementById("warning");

  const view = { x: 0, y: 0, scale: 1, touched: false };
  let graph = { nodes: [], edges: [] };
  let alpha = 0;
  let frame = null;
  let selected = null;
  const hiddenTypes = new Set();

  function svg(name, attributes, text) {
    const element = document.createElementNS(SVG_NS, name);
    for (const key of Object.keys(attributes || {})) {
      element.setAttribute(key, attributes[key]);
    }
    if (text !== undefined) {
      element.textContent = text;
    }
    return element;
  }

  function html(name, text) {
    const element = document.createElement(name);
    if (text !== undefined) {
      element.textContent = text;
    }
    return element;
  }

  function clear(element) {
    while (element.firstChild) {
      element.removeChild(element.firstChild);
    }
  }

  function isGraph(value) {
    return Boolean(value) && Array.isArray(value.nodes) && Array.isArray(value.edges);
  }

  function load(data) {
    if (!isGraph(data)) {
      summary.textContent = "That file is not a govgraph export: it needs 'nodes' and 'edges'.";
      return;
    }
    const roots = new Set(Array.isArray(data.roots) ? data.roots : []);
    const byId = new Map();
    data.nodes.forEach(function (raw, index) {
      if (!raw || typeof raw.id !== "string" || byId.has(raw.id)) {
        return;
      }
      // A sunflower spiral gives every node a distinct, repeatable starting point.
      const angle = index * 2.399963;
      const radius = 28 * Math.sqrt(index + 1);
      byId.set(raw.id, {
        id: raw.id,
        type: String(raw.type || "Unknown"),
        label: String(raw.label || raw.id),
        properties: raw.properties && typeof raw.properties === "object" ? raw.properties : {},
        root: roots.has(raw.id),
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        held: false,
      });
    });
    const edges = [];
    data.edges.forEach(function (raw) {
      const source = raw && byId.get(raw.source);
      const target = raw && byId.get(raw.target);
      if (source && target) {
        edges.push({
          source: source,
          target: target,
          type: String(raw.type || ""),
          label: String(raw.label || raw.type || ""),
        });
      }
    });
    graph = { nodes: Array.from(byId.values()), edges: edges };
    selected = null;
    hiddenTypes.clear();
    view.touched = false;
    warning.hidden = data.truncated !== true;
    summary.textContent = graph.nodes.length + " nodes, " + graph.edges.length + " edges";
    draw();
    showDetails(null);
    alpha = 1;
    start();
  }

  function colourOf(type, types) {
    return PALETTE[types.indexOf(type) % PALETTE.length];
  }

  function draw() {
    const types = Array.from(new Set(graph.nodes.map(function (n) { return n.type; }))).sort();
    clear(edgeLayer);
    clear(nodeLayer);
    clear(legend);

    graph.edges.forEach(function (edge) {
      edge.line = svg("line", { class: "edge", "marker-end": "url(#arrow)" });
      edge.line.appendChild(svg("title", {}, edge.type));
      edge.text = svg("text", { class: "edge-label" }, edge.label);
      edge.text.style.display = "none";
      edgeLayer.appendChild(edge.line);
      edgeLayer.appendChild(edge.text);
    });

    graph.nodes.forEach(function (node) {
      const group = svg("g", {
        class: node.root ? "node root" : "node",
        tabindex: "0",
        role: "button",
        "aria-label": node.type + " " + node.label,
      });
      group.appendChild(svg("circle", { r: NODE_RADIUS, fill: colourOf(node.type, types) }));
      group.appendChild(svg("text", { x: NODE_RADIUS + 4, y: 4 }, node.label));
      group.appendChild(svg("title", {}, node.type + ": " + node.label));
      group.addEventListener("pointerdown", function (event) { beginDrag(event, node); });
      group.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select(node);
        }
      });
      node.group = group;
      nodeLayer.appendChild(group);
    });

    types.forEach(function (type) {
      const item = html("li");
      const label = html("label");
      const box = html("input");
      box.type = "checkbox";
      box.checked = true;
      box.addEventListener("change", function () {
        if (box.checked) {
          hiddenTypes.delete(type);
        } else {
          hiddenTypes.add(type);
        }
        applyVisibility();
      });
      const swatch = html("span");
      swatch.className = "swatch";
      swatch.style.background = colourOf(type, types);
      const count = graph.nodes.filter(function (n) { return n.type === type; }).length;
      label.appendChild(box);
      label.appendChild(swatch);
      label.appendChild(html("span", type + " (" + count + ")"));
      item.appendChild(label);
      legend.appendChild(item);
    });
    place();
  }

  function visible(node) {
    return !hiddenTypes.has(node.type);
  }

  function applyVisibility() {
    graph.nodes.forEach(function (node) {
      node.group.style.display = visible(node) ? "" : "none";
    });
    graph.edges.forEach(function (edge) {
      const shown = visible(edge.source) && visible(edge.target);
      edge.line.style.display = shown ? "" : "none";
    });
    if (selected && !visible(selected)) {
      select(null);
    } else {
      highlight();
    }
  }

  function place() {
    graph.nodes.forEach(function (node) {
      node.group.setAttribute("transform", "translate(" + node.x + "," + node.y + ")");
    });
    graph.edges.forEach(function (edge) {
      const dx = edge.target.x - edge.source.x;
      const dy = edge.target.y - edge.source.y;
      const length = Math.hypot(dx, dy) || 1;
      const ux = dx / length;
      const uy = dy / length;
      // Stop at the rim of each circle so the arrowhead is not hidden under the node.
      edge.line.setAttribute("x1", edge.source.x + ux * NODE_RADIUS);
      edge.line.setAttribute("y1", edge.source.y + uy * NODE_RADIUS);
      edge.line.setAttribute("x2", edge.target.x - ux * (NODE_RADIUS + 2));
      edge.line.setAttribute("y2", edge.target.y - uy * (NODE_RADIUS + 2));
      edge.text.setAttribute("x", (edge.source.x + edge.target.x) / 2);
      edge.text.setAttribute("y", (edge.source.y + edge.target.y) / 2 - 3);
    });
  }

  function step() {
    const nodes = graph.nodes;
    for (let i = 0; i < nodes.length; i += 1) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j += 1) {
        const b = nodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let distanceSquared = dx * dx + dy * dy;
        if (distanceSquared < 1) {
          // Two nodes on the same spot: nudge them apart in a repeatable direction.
          dx = (i % 2 ? 1 : -1) * 0.5;
          dy = (j % 2 ? 1 : -1) * 0.5;
          distanceSquared = 1;
        }
        const push = (11000 * alpha) / distanceSquared;
        const distance = Math.sqrt(distanceSquared);
        const fx = (dx / distance) * push;
        const fy = (dy / distance) * push;
        a.vx -= fx;
        a.vy -= fy;
        b.vx += fx;
        b.vy += fy;
      }
    }
    graph.edges.forEach(function (edge) {
      const dx = edge.target.x - edge.source.x;
      const dy = edge.target.y - edge.source.y;
      const distance = Math.hypot(dx, dy) || 1;
      const pull = ((distance - REST_LENGTH) / distance) * 0.06 * alpha;
      edge.source.vx += dx * pull;
      edge.source.vy += dy * pull;
      edge.target.vx -= dx * pull;
      edge.target.vy -= dy * pull;
    });
    nodes.forEach(function (node) {
      if (node.held) {
        node.vx = 0;
        node.vy = 0;
        return;
      }
      node.vx = (node.vx - node.x * 0.012 * alpha) * 0.82;
      node.vy = (node.vy - node.y * 0.012 * alpha) * 0.82;
      node.x += node.vx;
      node.y += node.vy;
    });
    alpha *= 0.985;
  }

  function fit() {
    const shown = graph.nodes.filter(visible);
    if (!shown.length) {
      return;
    }
    const xs = shown.map(function (n) { return n.x; });
    const ys = shown.map(function (n) { return n.y; });
    const left = Math.min.apply(null, xs) - 40;
    const top = Math.min.apply(null, ys) - 40;
    const width = Math.max.apply(null, xs) - left + 200;
    const height = Math.max.apply(null, ys) - top + 40;
    const box = canvas.getBoundingClientRect();
    view.scale = Math.max(0.1, Math.min(2.5, box.width / width, box.height / height));
    view.x = (box.width - width * view.scale) / 2 - left * view.scale;
    view.y = (box.height - height * view.scale) / 2 - top * view.scale;
    applyView();
  }

  function applyView() {
    viewport.setAttribute(
      "transform",
      "translate(" + view.x + "," + view.y + ") scale(" + view.scale + ")"
    );
  }

  function tick() {
    frame = null;
    step();
    place();
    if (!view.touched) {
      fit();
    }
    if (alpha > 0.01) {
      start();
    }
  }

  function start() {
    if (frame === null) {
      frame = window.requestAnimationFrame(tick);
    }
  }

  function reheat(amount) {
    alpha = Math.max(alpha, amount);
    start();
  }

  function highlight() {
    const near = new Set();
    if (selected) {
      near.add(selected);
    }
    graph.edges.forEach(function (edge) {
      const touches = selected !== null && (edge.source === selected || edge.target === selected);
      edge.line.classList.toggle("near", touches);
      edge.line.classList.toggle("faded", selected !== null && !touches);
      const shown = touches && visible(edge.source) && visible(edge.target);
      edge.text.style.display = shown ? "" : "none";
      if (touches) {
        near.add(edge.source);
        near.add(edge.target);
      }
    });
    graph.nodes.forEach(function (node) {
      node.group.classList.toggle("selected", node === selected);
      node.group.classList.toggle("faded", selected !== null && !near.has(node));
    });
  }

  function showDetails(node) {
    clear(details);
    if (!node) {
      details.appendChild(
        html("p", "Select a node to see its properties. Drag nodes to move them, drag the " +
          "background to pan and scroll to zoom.")
      );
      return;
    }
    const list = html("dl");
    function row(name, value, asCode) {
      list.appendChild(html("dt", name));
      const cell = html("dd");
      cell.appendChild(html(asCode ? "code" : "span", String(value)));
      list.appendChild(cell);
    }
    row("type", node.type);
    row("id", node.id, true);
    Object.keys(node.properties).sort().forEach(function (key) {
      row(key, node.properties[key]);
    });
    details.appendChild(list);
  }

  function select(node) {
    selected = node === selected ? null : node;
    showDetails(selected);
    highlight();
  }

  function toGraph(event) {
    const box = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - box.left - view.x) / view.scale,
      y: (event.clientY - box.top - view.y) / view.scale,
    };
  }

  function beginDrag(event, node) {
    event.stopPropagation();
    const start = toGraph(event);
    const offset = { x: node.x - start.x, y: node.y - start.y };
    let moved = false;
    node.held = true;
    view.touched = true;
    node.group.setPointerCapture(event.pointerId);

    function move(moveEvent) {
      const point = toGraph(moveEvent);
      if (Math.hypot(point.x - start.x, point.y - start.y) > 3) {
        moved = true;
      }
      node.x = point.x + offset.x;
      node.y = point.y + offset.y;
      reheat(0.25);
      place();
    }
    function end() {
      node.held = false;
      node.group.removeEventListener("pointermove", move);
      node.group.removeEventListener("pointerup", end);
      node.group.removeEventListener("pointercancel", end);
      if (!moved) {
        select(node);
      }
    }
    node.group.addEventListener("pointermove", move);
    node.group.addEventListener("pointerup", end);
    node.group.addEventListener("pointercancel", end);
  }

  canvas.addEventListener("pointerdown", function (event) {
    const origin = { x: event.clientX - view.x, y: event.clientY - view.y };
    let moved = false;
    canvas.classList.add("panning");
    canvas.setPointerCapture(event.pointerId);
    function move(moveEvent) {
      moved = true;
      view.touched = true;
      view.x = moveEvent.clientX - origin.x;
      view.y = moveEvent.clientY - origin.y;
      applyView();
    }
    function end() {
      canvas.classList.remove("panning");
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", end);
      canvas.removeEventListener("pointercancel", end);
      if (!moved && selected) {
        select(null);
      }
    }
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);
  });

  canvas.addEventListener("wheel", function (event) {
    event.preventDefault();
    const box = canvas.getBoundingClientRect();
    const px = event.clientX - box.left;
    const py = event.clientY - box.top;
    const scale = Math.max(0.1, Math.min(5, view.scale * Math.exp(-event.deltaY * 0.0015)));
    // Keep the point under the pointer where it is while the scale changes.
    view.x = px - ((px - view.x) / view.scale) * scale;
    view.y = py - ((py - view.y) / view.scale) * scale;
    view.scale = scale;
    view.touched = true;
    applyView();
  }, { passive: false });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && selected) {
      select(null);
    }
  });

  document.getElementById("file").addEventListener("change", function (event) {
    const file = event.target.files && event.target.files[0];
    if (!file) {
      return;
    }
    const reader = new FileReader();
    reader.onload = function () {
      let data = null;
      try {
        data = JSON.parse(String(reader.result));
      } catch (error) {
        summary.textContent = "That file is not valid JSON.";
        return;
      }
      load(data);
    };
    reader.readAsText(file);
  });

  if (EMBEDDED_GRAPH !== null) {
    load(EMBEDDED_GRAPH);
  }
})();
