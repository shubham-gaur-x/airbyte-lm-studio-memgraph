const pptxgen = require("pptxgenjs");
const path = require("path");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const {
  FaBolt, FaBrain, FaClock, FaSitemap, FaSearch, FaComments,
  FaDatabase, FaCubes, FaCheckCircle, FaChartLine, FaProjectDiagram,
  FaRobot, FaLock, FaLayerGroup, FaNetworkWired,
} = require("react-icons/fa");

const ASSETS = path.join(__dirname, "..");

// ---------------------------------------------------------------------------
// Palette — extracted directly from the existing meeting-memory-v4.pptx deck
// ---------------------------------------------------------------------------
const C = {
  bg: "1A1F3C",        // slide background (dark navy)
  bgDeep: "0D1529",     // deeper navy for nested panels
  card: "1E293B",       // card background
  card2: "1E3A5F",      // alternate card background
  teal: "0D9488",
  blue: "0EA5E9",
  green: "10B981",
  purple: "5C67F2",
  amber: "F59E0B",
  red: "E11D48",
  white: "FFFFFF",
  muted: "64748B",
  light: "E2E8F0",
  lighter: "CBD5E1",
  faint: "F0F9FB",
};

function iconSvg(IconComponent, color, size = 256) {
  return ReactDOMServer.renderToStaticMarkup(
    React.createElement(IconComponent, { color, size: String(size) })
  );
}
async function iconPng(IconComponent, color, size = 256) {
  const svg = iconSvg(IconComponent, color, size);
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

async function main() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9";
  pres.author = "Shubham Gaur";
  pres.title = "Meeting Memory v4 — Graph Intelligence Layer";

  const W = 10, H = 5.625;

  // Preload icons used across multiple slides
  const icons = {
    bolt: await iconPng(FaBolt, "#F59E0B"),
    brain: await iconPng(FaBrain, "#0D9488"),
    clock: await iconPng(FaClock, "#0EA5E9"),
    sitemap: await iconPng(FaSitemap, "#5C67F2"),
    search: await iconPng(FaSearch, "#10B981"),
    comments: await iconPng(FaComments, "#E11D48"),
    database: await iconPng(FaDatabase, "#0EA5E9"),
    cubes: await iconPng(FaCubes, "#5C67F2"),
    check: await iconPng(FaCheckCircle, "#10B981"),
    chart: await iconPng(FaChartLine, "#F59E0B"),
    project: await iconPng(FaProjectDiagram, "#0D9488"),
    robot: await iconPng(FaRobot, "#5C67F2"),
    lock: await iconPng(FaLock, "#10B981"),
    layer: await iconPng(FaLayerGroup, "#0EA5E9"),
    network: await iconPng(FaNetworkWired, "#E11D48"),
  };

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------
  function baseSlide() {
    const s = pres.addSlide();
    s.background = { color: C.bg };
    return s;
  }

  function kicker(s, text) {
    s.addText(text.toUpperCase(), {
      x: 0.5, y: 0.28, w: 9, h: 0.3, fontSize: 11, charSpacing: 2,
      color: C.teal, bold: true, fontFace: "Calibri", margin: 0,
    });
  }

  function title(s, text, opts = {}) {
    s.addText(text, {
      x: 0.5, y: 0.55, w: opts.w || 9, h: 0.6, fontSize: opts.size || 28,
      color: C.white, bold: true, fontFace: "Cambria", margin: 0,
    });
  }

  function footer(s, pageNum) {
    s.addText("Meeting Memory v4 — Graph Intelligence Layer", {
      x: 0.5, y: H - 0.35, w: 6, h: 0.25, fontSize: 8, color: C.muted, margin: 0,
    });
    s.addText(String(pageNum), {
      x: W - 0.9, y: H - 0.35, w: 0.4, h: 0.25, fontSize: 8, color: C.muted,
      align: "right", margin: 0,
    });
  }

  function iconCircle(s, iconData, x, y, d, bgColor) {
    s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: bgColor } });
    const pad = d * 0.26;
    s.addImage({ data: iconData, x: x + pad, y: y + pad, w: d - pad * 2, h: d - pad * 2 });
  }

  function card(s, x, y, w, h, opts = {}) {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w, h, rectRadius: 0.08,
      fill: { color: opts.fill || C.card },
      line: { color: opts.line || "334155", width: 0.75 },
    });
  }

  // =========================================================================
  // SLIDE 1 — Title
  // =========================================================================
  {
    const s = baseSlide();
    s.addText("Meeting Memory", {
      x: 0.7, y: 1.55, w: 8.6, h: 0.85, fontSize: 44, bold: true,
      color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addText([
      { text: "v4", options: { color: C.teal, bold: true } },
      { text: "  —  Graph Intelligence Layer", options: { color: C.lighter } },
    ], { x: 0.7, y: 2.35, w: 8.6, h: 0.5, fontSize: 22, fontFace: "Calibri", margin: 0 });

    s.addText(
      "Advanced algorithms · semantic, episodic & procedural memory · vector search — " +
      "built on the local-first pipeline, fully grounded, nothing leaves the Mac.",
      { x: 0.7, y: 2.95, w: 8.0, h: 0.6, fontSize: 13, color: C.muted, fontFace: "Calibri", margin: 0 }
    );

    // Icon row — the five new capabilities
    const items = [
      [icons.chart, "Algorithms", C.amber],
      [icons.brain, "Semantic", C.teal],
      [icons.clock, "Episodic", C.blue],
      [icons.sitemap, "Procedural", C.purple],
      [icons.search, "Vector", C.green],
    ];
    let ix = 0.7;
    const gap = 1.75;
    items.forEach(([icon, label, color]) => {
      iconCircle(s, icon, ix, 3.85, 0.62, color);
      s.addText(label, {
        x: ix - 0.25, y: 4.52, w: 1.1, h: 0.3, fontSize: 10.5, color: C.lighter,
        align: "center", fontFace: "Calibri", margin: 0,
      });
      ix += gap;
    });

    s.addText("Shubham Gaur  ·  July 2026", {
      x: 0.7, y: H - 0.55, w: 5, h: 0.3, fontSize: 10, color: C.muted, margin: 0,
    });
  }

  // =========================================================================
  // SLIDE 2 — Recap: what already existed
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "Where we left off");
    title(s, "The Pipeline You've Already Seen");

    const steps = [
      ["Gmail · Calendar · Jira", "Airbyte Cloud, 3 connectors, incremental sync", icons.database, C.blue],
      ["Local Postgres", "Docker, exactly-once processing", icons.layer, C.teal],
      ["LM Studio + Gemma3:12b", "Local extraction, zero cloud inference", icons.robot, C.purple],
      ["Local Memgraph", "Person · Meeting · Topic · Decision · ActionItem graph", icons.project, C.amber],
    ];
    let y = 1.55;
    steps.forEach(([h, d, icon, color]) => {
      iconCircle(s, icon, 0.6, y, 0.5, color);
      s.addText(h, { x: 1.35, y: y - 0.03, w: 4.2, h: 0.32, fontSize: 14, bold: true, color: C.white, fontFace: "Calibri", margin: 0 });
      s.addText(d, { x: 1.35, y: y + 0.28, w: 5.0, h: 0.4, fontSize: 10.5, color: C.muted, fontFace: "Calibri", margin: 0 });
      y += 0.85;
    });

    card(s, 6.3, 1.5, 3.1, 3.5, { fill: C.bgDeep });
    s.addText("This session adds", {
      x: 6.55, y: 1.68, w: 2.6, h: 0.3, fontSize: 12, bold: true, color: C.teal, fontFace: "Calibri", margin: 0,
    });
    const adds = [
      "5 graph algorithms",
      "Durable fact memory",
      "Temporal event chains",
      "Auto-detected workflows",
      "Semantic vector search",
      "One NL query interface",
    ];
    s.addText(adds.map((t) => ({ text: t, options: { bullet: { code: "2022" }, breakLine: true, color: C.light } })), {
      x: 6.55, y: 2.05, w: 2.6, h: 2.1, fontSize: 11.5, fontFace: "Calibri", margin: 0,
      valign: "top", lineSpacingMultiple: 1.35,
    });

    footer(s, 2);
  }

  // =========================================================================
  // SLIDE 3 — Full Architecture (Updated)
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "System design");
    title(s, "Full Architecture — Updated");

    // Row 1: sources -> airbyte -> postgres
    const boxes = [
      { x: 0.5, y: 1.55, w: 1.55, h: 0.85, t: "Gmail\nCalendar\nJira", c: C.blue },
      { x: 2.3, y: 1.55, w: 1.55, h: 0.85, t: "Airbyte\nCloud", c: C.teal },
      { x: 4.1, y: 1.55, w: 1.55, h: 0.85, t: "Local\nPostgres", c: C.blue },
      { x: 5.9, y: 1.55, w: 1.6, h: 0.85, t: "Transform\nService", c: C.purple },
      { x: 7.75, y: 1.55, w: 1.75, h: 0.85, t: "LM Studio\ngemma3 + nomic-embed", c: C.amber },
    ];
    boxes.forEach((b) => {
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: b.x, y: b.y, w: b.w, h: b.h, rectRadius: 0.06,
        fill: { color: C.card }, line: { color: b.c, width: 1.25 },
      });
      s.addText(b.t, {
        x: b.x, y: b.y, w: b.w, h: b.h, fontSize: 9.5, color: C.white, bold: true,
        align: "center", valign: "middle", fontFace: "Calibri", margin: 2,
      });
    });
    // arrows row1
    [[2.05, 1.975], [3.85, 1.975], [5.65, 1.975], [7.5, 1.975]].forEach(([x, y]) => {
      s.addShape(pres.shapes.RIGHT_ARROW, { x, y: y - 0.09, w: 0.25, h: 0.18, fill: { color: C.muted } });
    });

    // Row 2: Memgraph 3.11.0 engine <-> Lab, MCP, memory modules
    card(s, 0.5, 2.75, 4.55, 2.15, { fill: C.bgDeep, line: C.teal });
    s.addText("Memgraph 3.11.0 (memgraph-mage)", {
      x: 0.7, y: 2.87, w: 4.2, h: 0.3, fontSize: 12, bold: true, color: C.teal, fontFace: "Calibri", margin: 0,
    });
    const modules = [
      "graph_algorithms.py — MAGE CALL procedures (only place)",
      "semantic_memory.py — Fact, Preference, KNOWS",
      "episodic_memory.py — PRECEDED_BY, decay, sessions",
      "procedural_memory.py — Procedure, ProcedureStep",
      "vector_memory.py — embeddings + vector_search",
    ];
    s.addText(modules.map((t) => ({ text: t, options: { bullet: { code: "2022" }, breakLine: true } })), {
      x: 0.7, y: 3.2, w: 4.2, h: 1.6, fontSize: 9.5, color: C.light, fontFace: "Calibri", margin: 0, lineSpacingMultiple: 1.3,
    });

    card(s, 5.3, 2.75, 2.05, 2.15, { fill: C.bgDeep, line: C.blue });
    s.addText("Memgraph Lab\n3.11.0", {
      x: 5.45, y: 2.9, w: 1.8, h: 0.5, fontSize: 12, bold: true, color: C.blue, fontFace: "Calibri", margin: 0,
    });
    s.addText("Standalone container · localhost:3000 · visual query + graph view", {
      x: 5.45, y: 3.4, w: 1.8, h: 1.3, fontSize: 9, color: C.muted, fontFace: "Calibri", margin: 0,
    });

    card(s, 7.55, 2.75, 1.95, 2.15, { fill: C.bgDeep, line: C.purple });
    s.addText("Memgraph MCP", {
      x: 7.7, y: 2.9, w: 1.7, h: 0.4, fontSize: 12, bold: true, color: C.purple, fontFace: "Calibri", margin: 0,
    });
    s.addText("Docker sidecar · Claude Desktop / any agent, read+write", {
      x: 7.7, y: 3.4, w: 1.7, h: 1.3, fontSize: 9, color: C.muted, fontFace: "Calibri", margin: 0,
    });

    s.addText("100% local — docker compose up. Zero cloud inference after Airbyte sync.", {
      x: 0.5, y: 5.1, w: 9, h: 0.3, fontSize: 10, italic: true, color: C.muted, align: "center", fontFace: "Calibri", margin: 0,
    });
    footer(s, 3);
  }

  // =========================================================================
  // SLIDE 4 — Graph Intelligence Overview
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "What's new");
    title(s, "The Graph Got Smarter");
    s.addText("Same ingestion pipeline. Five new capabilities computed on top of it, automatically.", {
      x: 0.5, y: 1.15, w: 9, h: 0.35, fontSize: 12, color: C.muted, fontFace: "Calibri", margin: 0,
    });

    const cells = [
      [icons.chart, "Advanced Algorithms", "PageRank, community detection, betweenness & degree centrality — who actually matters.", C.amber],
      [icons.brain, "Semantic Memory", "Durable facts and preferences that persist and gain confidence over time.", C.teal],
      [icons.clock, "Episodic Memory", "Temporal chains between meetings, plus automatic relevance decay.", C.blue],
      [icons.sitemap, "Procedural Memory", "Recognizes known workflows and discovers new recurring patterns on its own.", C.purple],
      [icons.search, "Vector Search", "Semantic — not keyword — search over every meeting summary and fact.", C.green],
      [icons.comments, "Memory Retrieval", "One natural-language interface over all four layers, fully grounded.", C.red],
    ];
    const cw = 2.95, ch = 1.55, gx = 0.15, gy = 0.15;
    let cx = 0.5, cy = 1.7;
    cells.forEach(([icon, h, d, color], i) => {
      card(s, cx, cy, cw, ch);
      iconCircle(s, icon, cx + 0.18, cy + 0.18, 0.42, color);
      s.addText(h, { x: cx + 0.7, y: cy + 0.15, w: cw - 0.85, h: 0.5, fontSize: 11.5, bold: true, color: C.white, fontFace: "Calibri", margin: 0 });
      s.addText(d, { x: cx + 0.18, y: cy + 0.68, w: cw - 0.36, h: ch - 0.8, fontSize: 9, color: C.muted, fontFace: "Calibri", margin: 0 });
      if ((i + 1) % 3 === 0) { cx = 0.5; cy += ch + gy; } else { cx += cw + gx; }
    });
    footer(s, 4);
  }

  // =========================================================================
  // SLIDE 5 — Advanced Algorithms (screenshot)
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "Graph algorithms");
    title(s, "Who Actually Matters Here?");

    s.addImage({
      path: path.join(ASSETS, "memgraph_01_influential_graph.png"),
      x: 4.55, y: 1.35, w: 5.0, h: 3.75, sizing: { type: "contain", w: 5.0, h: 3.75 },
    });
    card(s, 4.5, 1.3, 5.05, 3.85, { fill: "FFFFFF", line: "E2E8F0" });
    s.addImage({
      path: path.join(ASSETS, "memgraph_01_influential_graph.png"),
      x: 4.65, y: 1.42, w: 4.8, h: 3.6, sizing: { type: "contain", w: 4.8, h: 3.6 },
    });
    s.addText("Real query · people sized/colored by PageRank, connected to their meetings", {
      x: 4.5, y: 5.16, w: 5.05, h: 0.25, fontSize: 8, italic: true, color: C.muted, align: "center", margin: 0,
    });

    s.addText(
      "PageRank — the same algorithm Google used for web pages — applied to meeting " +
      "attendance. Not “who attends the most meetings,” but “who's central to the " +
      "meetings that matter.” Community detection groups people into teams automatically, " +
      "no org chart required.",
      { x: 0.5, y: 1.5, w: 3.8, h: 1.9, fontSize: 11.5, color: C.light, fontFace: "Calibri", margin: 0, lineSpacingMultiple: 1.3 }
    );

    card(s, 0.5, 3.55, 3.8, 1.55, { fill: C.bgDeep });
    s.addText("Top by PageRank (live)", { x: 0.68, y: 3.68, w: 3.4, h: 0.28, fontSize: 10, bold: true, color: C.amber, fontFace: "Calibri", margin: 0 });
    const top3 = ["1. Femi Oduwole — 0.00474", "2. Matteo Vaiente — 0.00459", "3. Mark Johnston — 0.00369"];
    s.addText(top3.map((t) => ({ text: t, options: { breakLine: true } })), {
      x: 0.68, y: 3.98, w: 3.4, h: 1.0, fontSize: 10, color: C.light, fontFace: "Courier New", margin: 0, lineSpacingMultiple: 1.3,
    });

    footer(s, 5);
  }

  // =========================================================================
  // SLIDE 6 — Semantic Memory (screenshot)
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "Semantic memory");
    title(s, "The Graph Remembers Facts, Not Just Events");

    card(s, 0.5, 1.3, 5.05, 3.85, { fill: "FFFFFF", line: "E2E8F0" });
    s.addImage({
      path: path.join(ASSETS, "memgraph_04_has_fact_semantic.png"),
      x: 0.65, y: 1.42, w: 4.8, h: 3.6, sizing: { type: "contain", w: 4.8, h: 3.6 },
    });
    s.addText("Real query · MATCH (m:Meeting)-[:HAS_FACT]->(f:Fact)", {
      x: 0.5, y: 5.16, w: 5.05, h: 0.25, fontSize: 8, italic: true, color: C.muted, align: "center", margin: 0,
    });

    s.addText(
      "Every meeting summary is run through the local LLM to extract durable facts — not " +
      "what happened, but what's now permanently true. Confidence rises every time a fact " +
      "gets independently reconfirmed.",
      { x: 5.75, y: 1.45, w: 3.75, h: 1.2, fontSize: 11, color: C.light, fontFace: "Calibri", margin: 0, lineSpacingMultiple: 1.3 }
    );

    card(s, 5.75, 2.7, 3.75, 2.45, { fill: C.bgDeep });
    s.addText("Live facts in the graph", { x: 5.93, y: 2.82, w: 3.4, h: 0.28, fontSize: 10, bold: true, color: C.teal, fontFace: "Calibri", margin: 0 });
    const facts = [
      "“Femi leads the QA automation initiative”",
      "“The QA AI pilot is ongoing at Canadian Blood Services”",
      "“Jacob will own the integration with the test suite”",
    ];
    s.addText(facts.map((t) => ({ text: t, options: { bullet: { code: "2022" }, breakLine: true } })), {
      x: 5.93, y: 3.13, w: 3.4, h: 1.9, fontSize: 9.5, color: C.light, italic: true, fontFace: "Calibri", margin: 0, lineSpacingMultiple: 1.35,
    });

    footer(s, 6);
  }

  // =========================================================================
  // SLIDE 7 — Episodic + Procedural Memory (screenshot)
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "Episodic & procedural memory");
    title(s, "Time-Aware, and It Recognized Our Process");

    s.addText(
      "Meetings chain together over time — this standup followed that one, three days " +
      "apart — and relevance decays automatically, 5% per day. On top of that, the graph " +
      "matched a real 2-person meeting to the “one-on-one” workflow with zero manual " +
      "input, and can discover entirely new recurring patterns on its own.",
      { x: 0.5, y: 1.45, w: 3.85, h: 2.3, fontSize: 11, color: C.light, fontFace: "Calibri", margin: 0, lineSpacingMultiple: 1.35 }
    );

    card(s, 0.5, 3.85, 3.85, 1.3, { fill: C.bgDeep });
    s.addText([
      { text: "1 ", options: { color: C.purple, bold: true, fontSize: 20 } },
      { text: "real meeting auto-matched to ", options: { color: C.light, fontSize: 10.5 } },
      { text: "one_on_one", options: { color: C.purple, bold: true, fontSize: 10.5, fontFace: "Courier New" } },
    ], { x: 0.68, y: 4.0, w: 3.5, h: 0.5, margin: 0, fontFace: "Calibri" });
    s.addText("6 known workflow templates seeded · discovery job runs nightly", {
      x: 0.68, y: 4.55, w: 3.5, h: 0.5, fontSize: 9.5, color: C.muted, fontFace: "Calibri", margin: 0,
    });

    card(s, 4.55, 1.3, 5.0, 3.85, { fill: "FFFFFF", line: "E2E8F0" });
    s.addImage({
      path: path.join(ASSETS, "memgraph_03_procedure_steps.png"),
      x: 4.7, y: 1.42, w: 4.75, h: 3.6, sizing: { type: "contain", w: 4.75, h: 3.6 },
    });
    s.addText("Real query · sprint_planning procedure and its ordered steps", {
      x: 4.55, y: 5.16, w: 5.0, h: 0.25, fontSize: 8, italic: true, color: C.muted, align: "center", margin: 0,
    });

    footer(s, 7);
  }

  // =========================================================================
  // SLIDE 8 — Vector Search (the wow moment)
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "Vector search");
    title(s, "It Understands Meaning, Not Keywords");

    card(s, 0.5, 1.4, 9, 1.15, { fill: C.bgDeep, line: C.green });
    s.addText("The question asked:", { x: 0.75, y: 1.52, w: 8.5, h: 0.28, fontSize: 10, color: C.muted, fontFace: "Calibri", margin: 0 });
    s.addText('"Who is responsible for testing automation?"', {
      x: 0.75, y: 1.8, w: 8.5, h: 0.5, fontSize: 18, bold: true, color: C.white, italic: true, fontFace: "Cambria", margin: 0,
    });

    s.addShape(pres.shapes.DOWN_ARROW, { x: 4.75, y: 2.68, w: 0.5, h: 0.32, fill: { color: C.green } });

    card(s, 0.5, 3.15, 9, 1.5, { fill: C.card, line: C.green });
    s.addText([
      { text: "Top match  ", options: { color: C.green, bold: true, fontSize: 11 } },
      { text: "(65% similarity, zero keyword overlap):", options: { color: C.muted, fontSize: 11 } },
    ], { x: 0.75, y: 3.32, w: 8.5, h: 0.3, fontFace: "Calibri", margin: 0 });
    s.addText('"Femi leads the QA automation initiative"', {
      x: 0.75, y: 3.65, w: 8.5, h: 0.5, fontSize: 16, bold: true, color: C.white, italic: true, fontFace: "Cambria", margin: 0,
    });
    s.addText("No word in the question appears in the fact. This is semantic understanding, not search-and-highlight.", {
      x: 0.75, y: 4.15, w: 8.5, h: 0.4, fontSize: 10, color: C.lighter, fontFace: "Calibri", margin: 0,
    });

    s.addText(
      "Every meeting summary and every fact is embedded (768-dim, LM Studio's nomic-embed-text) " +
      "the moment it's written. Two MAGE vector indexes power nearest-neighbor search directly in Memgraph.",
      { x: 0.5, y: 4.74, w: 9, h: 0.45, fontSize: 9.5, color: C.muted, fontFace: "Calibri", margin: 0 }
    );
    footer(s, 8);
  }

  // =========================================================================
  // SLIDE 9 — Memory Retrieval NL Query
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "Bringing it together");
    title(s, "One Natural-Language Interface, Fully Grounded");

    card(s, 0.5, 1.4, 9, 0.85, { fill: C.bgDeep });
    s.addText("POST /graph/memory/query", { x: 0.75, y: 1.5, w: 3, h: 0.3, fontSize: 10, color: C.blue, fontFace: "Courier New", margin: 0 });
    s.addText('{ "question": "What does Matteo discuss in his standups?" }', {
      x: 0.75, y: 1.8, w: 8.5, h: 0.35, fontSize: 11, color: C.light, fontFace: "Courier New", margin: 0,
    });

    s.addShape(pres.shapes.DOWN_ARROW, { x: 4.75, y: 2.35, w: 0.5, h: 0.28, fill: { color: C.blue } });

    card(s, 0.5, 2.75, 9, 1.55, { fill: C.card, line: C.blue });
    s.addText("Grounded answer:", { x: 0.75, y: 2.88, w: 8.5, h: 0.28, fontSize: 10, color: C.muted, fontFace: "Calibri", margin: 0 });
    s.addText(
      '"Matteo participates in daily CBS Standups on several dates: 2026-02-03, 2026-02-24, ' +
      'and 2026-03-02. These standups occur on Email."',
      { x: 0.75, y: 3.18, w: 8.5, h: 0.9, fontSize: 12.5, italic: true, color: C.white, fontFace: "Cambria", margin: 0, lineSpacingMultiple: 1.25 }
    );

    const trail = [
      [icons.search, "1. Extract entities", "Who / what / when, via LM Studio"],
      [icons.project, "2. Assemble context", "Facts, prefs, scores, meetings — capped at 20 nodes"],
      [icons.comments, "3. Synthesize", "Local LLM answers using ONLY that context"],
      [icons.clock, "4. Log session", "MemorySession + ACCESSED edges — full audit trail"],
    ];
    let tx = 0.5;
    trail.forEach(([icon, h, d]) => {
      iconCircle(s, icon, tx, 4.55, 0.32, C.card2);
      s.addText(h, { x: tx - 0.15, y: 4.9, w: 2.55, h: 0.25, fontSize: 8.5, bold: true, color: C.light, align: "center", fontFace: "Calibri", margin: 0 });
      tx += 2.3;
    });

    footer(s, 9);
  }

  // =========================================================================
  // SLIDE 10 — Tech Stack (Updated)
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "Under the hood");
    title(s, "Tech Stack — Updated");

    const cols = [
      ["Graph Engine", [
        "Memgraph 3.11.0 (memgraph-mage)",
        "MAGE: pagerank, community_detection,",
        "betweenness/degree centrality, WCC,",
        "vector_search, node_similarity",
        "Memgraph Lab 3.11.0 (standalone)",
      ], C.teal],
      ["LLM Inference", [
        "LM Studio — 100% local",
        "google/gemma-3-12b (chat/extraction)",
        "text-embedding-nomic-embed-text-v1.5",
        "768-dim embeddings, cosine similarity",
        "OpenAI-compatible API, one shared client",
      ], C.purple],
      ["New Modules", [
        "graph_algorithms.py",
        "semantic_memory.py",
        "episodic_memory.py",
        "procedural_memory.py",
        "vector_memory.py · memory_retrieval.py",
      ], C.amber],
    ];
    const cw = 2.95, gx = 0.15;
    let cx = 0.5;
    cols.forEach(([h, items, color]) => {
      card(s, cx, 1.5, cw, 3.6, { fill: C.bgDeep, line: color });
      s.addText(h, { x: cx + 0.2, y: 1.65, w: cw - 0.4, h: 0.35, fontSize: 13, bold: true, color, fontFace: "Calibri", margin: 0 });
      s.addText(items.map((t) => ({ text: t, options: { bullet: { code: "2022" }, breakLine: true } })), {
        x: cx + 0.2, y: 2.08, w: cw - 0.4, h: 2.9, fontSize: 9.5, color: C.light, fontFace: "Courier New", margin: 0,
        valign: "top", lineSpacingMultiple: 1.4,
      });
      cx += cw + gx;
    });
    footer(s, 10);
  }

  // =========================================================================
  // SLIDE 11 — What's Working Right Now (stats)
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "Live status");
    title(s, "What's Working Right Now");

    const stats = [
      ["99", "tests passing", C.green],
      ["74", "meetings in graph", C.blue],
      ["6", "known workflow templates", C.purple],
      ["768", "embedding dimensions", C.amber],
    ];
    let sx = 0.5;
    const sw = 2.2;
    stats.forEach(([num, label, color]) => {
      card(s, sx, 1.5, sw, 1.55, { fill: C.bgDeep });
      s.addText(num, { x: sx, y: 1.6, w: sw, h: 0.75, fontSize: 34, bold: true, color, align: "center", fontFace: "Cambria", margin: 0 });
      s.addText(label, { x: sx + 0.1, y: 2.35, w: sw - 0.2, h: 0.55, fontSize: 9.5, color: C.lighter, align: "center", fontFace: "Calibri", margin: 0 });
      sx += sw + 0.13;
    });

    card(s, 0.5, 3.3, 9, 1.85, { fill: C.card });
    s.addText("Node counts (live)", { x: 0.7, y: 3.42, w: 4, h: 0.28, fontSize: 11, bold: true, color: C.teal, fontFace: "Calibri", margin: 0 });
    const nodeRows = [
      ["Meeting", "74"], ["Topic", "43"], ["ProcedureStep", "26"], ["Person", "7"],
      ["Procedure", "6"], ["Fact", "5"], ["MemorySession", "2"], ["Preference", "2"],
    ];
    let nx = 0.7, ny = 3.75;
    nodeRows.forEach(([label, count], i) => {
      s.addText([
        { text: count + "  ", options: { color: C.white, bold: true, fontSize: 12 } },
        { text: label, options: { color: C.muted, fontSize: 10 } },
      ], { x: nx, y: ny, w: 2.15, h: 0.3, fontFace: "Calibri", margin: 0 });
      if ((i + 1) % 4 === 0) { nx = 0.7; ny += 0.55; } else { nx += 2.15; }
    });

    footer(s, 11);
  }

  // =========================================================================
  // SLIDE 12 — Demo Walkthrough
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "Runbook");
    title(s, "Demo Walkthrough (~12 min)");

    const steps = [
      ["0:00", "Frame it", "“The graph got smarter without touching ingestion.”"],
      ["1:00", "Advanced Algorithms", "GET /graph/insights/influential — who actually matters"],
      ["3:00", "Semantic Memory", "Facts + KNOWS — durable knowledge, not transient notes"],
      ["5:00", "Episodic Memory", "Temporal chains + relevance decay"],
      ["6:30", "Procedural Memory", "Auto-matched a real 1:1, zero manual input"],
      ["8:30", "Vector Search", "“who's responsible for testing automation?” — the wow moment"],
      ["11:00", "Memory Retrieval", "One NL query ties all four layers together"],
    ];
    let y = 1.5;
    steps.forEach(([t, h, d]) => {
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: 0.5, y, w: 0.75, h: 0.42, rectRadius: 0.06, fill: { color: C.card2 },
      });
      s.addText(t, { x: 0.5, y, w: 0.75, h: 0.42, fontSize: 10, bold: true, color: C.blue, align: "center", valign: "middle", fontFace: "Courier New", margin: 0 });
      s.addText(h, { x: 1.4, y: y - 0.02, w: 2.6, h: 0.3, fontSize: 11.5, bold: true, color: C.white, fontFace: "Calibri", margin: 0 });
      s.addText(d, { x: 4.05, y: y - 0.02, w: 5.4, h: 0.42, fontSize: 10, color: C.muted, fontFace: "Calibri", margin: 0 });
      y += 0.5;
    });

    footer(s, 12);
  }

  // =========================================================================
  // SLIDE 13 — What This Demonstrates
  // =========================================================================
  {
    const s = baseSlide();
    kicker(s, "Closing");
    title(s, "What This Demonstrates");

    const rows = [
      [icons.lock, "Fully local", "Algorithms, memory, and vector search all run on-Mac. Zero data leaves the machine after Airbyte sync."],
      [icons.network, "Native graph intelligence", "MAGE algorithms compute influence and structure directly in Memgraph — no separate analytics pipeline."],
      [icons.brain, "Grounded, not hallucinated", "Every NL answer traces back to real graph nodes, logged as an auditable MemorySession."],
      [icons.robot, "Agent-ready today", "Everything shown is already queryable by Claude Desktop or any agent via the MCP server, zero extra work."],
    ];
    let y = 1.5;
    rows.forEach(([icon, h, d]) => {
      iconCircle(s, icon, 0.5, y, 0.5, C.card2);
      s.addText(h, { x: 1.25, y: y - 0.03, w: 7.8, h: 0.3, fontSize: 13, bold: true, color: C.white, fontFace: "Calibri", margin: 0 });
      s.addText(d, { x: 1.25, y: y + 0.3, w: 7.8, h: 0.55, fontSize: 10.5, color: C.muted, fontFace: "Calibri", margin: 0 });
      y += 0.92;
    });

    footer(s, 13);
  }

  // =========================================================================
  // SLIDE 14 — Thank you / Q&A
  // =========================================================================
  {
    const s = baseSlide();
    s.addText("Questions?", {
      x: 0.7, y: 2.2, w: 8.6, h: 0.9, fontSize: 40, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addText("docker compose up — that's the entire deployment.", {
      x: 0.7, y: 3.05, w: 8.6, h: 0.4, fontSize: 14, color: C.teal, fontFace: "Calibri", margin: 0,
    });
    s.addText("Shubham Gaur  ·  meeting-memory-v4", {
      x: 0.7, y: H - 0.55, w: 5, h: 0.3, fontSize: 10, color: C.muted, margin: 0,
    });
  }

  const outPath = path.join(ASSETS, "Graph_Intelligence_Demo.pptx");
  await pres.writeFile({ fileName: outPath });
  console.log("Wrote " + outPath);
}

main().catch((e) => { console.error(e); process.exit(1); });
