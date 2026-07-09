const pptxgen = require("pptxgenjs");
const path = require("path");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const {
  FaBolt, FaBrain, FaClock, FaSitemap, FaSearch, FaComments,
  FaDatabase, FaCubes, FaCheckCircle, FaChartLine, FaProjectDiagram,
  FaRobot, FaLock, FaLayerGroup, FaNetworkWired, FaEnvelope, FaJira,
  FaCodeBranch, FaGithub, FaExclamationTriangle, FaPlug, FaShieldAlt,
  FaDollarSign, FaHistory, FaFlask, FaTools, FaUserTie, FaRoad,
} = require("react-icons/fa");

const ASSETS = path.join(__dirname, "..");

const C = {
  bg: "1A1F3C", bgDeep: "0D1529", card: "1E293B", card2: "1E3A5F",
  teal: "0D9488", blue: "0EA5E9", green: "10B981", purple: "5C67F2",
  amber: "F59E0B", red: "E11D48", white: "FFFFFF", muted: "64748B",
  light: "E2E8F0", lighter: "CBD5E1",
};

function iconSvg(IconComponent, color, size = 256) {
  return ReactDOMServer.renderToStaticMarkup(React.createElement(IconComponent, { color, size: String(size) }));
}
async function iconPng(IconComponent, color, size = 256) {
  const buf = await sharp(Buffer.from(iconSvg(IconComponent, color, size))).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

async function main() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9";
  pres.author = "Shubham Gaur";
  pres.title = "Meeting Memory v4 — Complete Overview";
  const W = 10, H = 5.625;

  // All icons render in white — the surrounding circle carries the color
  // semantics. Rendering icons in the same hue as their circle (the previous
  // approach) risks an icon becoming invisible whenever a slide happens to
  // pair it with a same-colored circle.
  const W_ = "#FFFFFF";
  const icons = {
    bolt: await iconPng(FaBolt, W_), brain: await iconPng(FaBrain, W_),
    clock: await iconPng(FaClock, W_), sitemap: await iconPng(FaSitemap, W_),
    search: await iconPng(FaSearch, W_), comments: await iconPng(FaComments, W_),
    database: await iconPng(FaDatabase, W_), cubes: await iconPng(FaCubes, W_),
    check: await iconPng(FaCheckCircle, W_), chart: await iconPng(FaChartLine, W_),
    project: await iconPng(FaProjectDiagram, W_), robot: await iconPng(FaRobot, W_),
    lock: await iconPng(FaLock, W_), layer: await iconPng(FaLayerGroup, W_),
    network: await iconPng(FaNetworkWired, W_), envelope: await iconPng(FaEnvelope, W_),
    jira: await iconPng(FaJira, W_), branch: await iconPng(FaCodeBranch, W_),
    github: await iconPng(FaGithub, W_), warning: await iconPng(FaExclamationTriangle, W_),
    plug: await iconPng(FaPlug, W_), shield: await iconPng(FaShieldAlt, W_),
    dollar: await iconPng(FaDollarSign, W_), history: await iconPng(FaHistory, W_),
    flask: await iconPng(FaFlask, W_), tools: await iconPng(FaTools, W_),
    userTie: await iconPng(FaUserTie, W_), road: await iconPng(FaRoad, W_),
  };

  function baseSlide() {
    const s = pres.addSlide();
    s.background = { color: C.bg };
    return s;
  }
  function kicker(s, text) {
    s.addText(text.toUpperCase(), { x: 0.5, y: 0.26, w: 9, h: 0.28, fontSize: 10.5, charSpacing: 2, color: C.teal, bold: true, fontFace: "Calibri", margin: 0 });
  }
  function title(s, text, size) {
    s.addText(text, { x: 0.5, y: 0.52, w: 9, h: 0.55, fontSize: size || 25, color: C.white, bold: true, fontFace: "Cambria", margin: 0 });
  }
  function footer(s, pageNum, total) {
    s.addText("Meeting Memory v4 — Complete Overview", { x: 0.5, y: H - 0.32, w: 6, h: 0.22, fontSize: 7.5, color: C.muted, margin: 0 });
    s.addText(`${pageNum} / ${total}`, { x: W - 0.9, y: H - 0.32, w: 0.4, h: 0.22, fontSize: 7.5, color: C.muted, align: "right", margin: 0 });
  }
  function iconCircle(s, iconData, x, y, d, bgColor) {
    s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: bgColor } });
    const pad = d * 0.26;
    s.addImage({ data: iconData, x: x + pad, y: y + pad, w: d - pad * 2, h: d - pad * 2 });
  }
  function card(s, x, y, w, h, opts = {}) {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.07, fill: { color: opts.fill || C.card }, line: { color: opts.line || "334155", width: opts.lineW || 0.75 } });
  }
  function bulletList(s, items, x, y, w, h, opts = {}) {
    s.addText(items.map((t) => ({ text: t, options: { bullet: { code: "2022" }, breakLine: true } })), {
      x, y, w, h, fontSize: opts.fontSize || 9, color: opts.color || C.light, fontFace: opts.fontFace || "Calibri",
      margin: 0, valign: "top", lineSpacingMultiple: opts.lineSpacing || 1.28,
    });
  }
  function codeBlock(s, lines, x, y, w, h, opts = {}) {
    card(s, x, y, w, h, { fill: C.bgDeep, line: opts.line || "334155" });
    s.addText(lines.map((t) => ({ text: t, options: { breakLine: true } })), {
      x: x + 0.15, y: y + 0.1, w: w - 0.3, h: h - 0.2, fontSize: opts.fontSize || 8.3,
      color: opts.color || C.green, fontFace: "Courier New", margin: 0, valign: "top", lineSpacingMultiple: 1.25,
    });
  }

  const TOTAL = 27;

  // ===========================================================================
  // 1 — Title
  // ===========================================================================
  {
    const s = baseSlide();
    s.addText("Meeting Memory", { x: 0.7, y: 1.35, w: 8.6, h: 0.8, fontSize: 40, bold: true, color: C.white, fontFace: "Cambria", margin: 0 });
    s.addText([
      { text: "v4", options: { color: C.teal, bold: true } },
      { text: "  —  Complete Overview", options: { color: C.lighter } },
    ], { x: 0.7, y: 2.1, w: 8.6, h: 0.45, fontSize: 19, fontFace: "Calibri", margin: 0 });
    s.addText(
      "Business case, architecture, code, and live proof — one fully local pipeline, zero cloud inference.",
      { x: 0.7, y: 2.6, w: 8.2, h: 0.4, fontSize: 12, color: C.muted, fontFace: "Calibri", margin: 0 }
    );
    const items = [
      [icons.userTie, "Business", C.amber], [icons.project, "Architecture", C.teal],
      [icons.brain, "Memory", C.purple], [icons.flask, "Proof", C.blue],
      [icons.road, "What's next", C.red],
    ];
    let ix = 0.7;
    items.forEach(([icon, label, color]) => {
      iconCircle(s, icon, ix, 3.35, 0.58, color);
      s.addText(label, { x: ix - 0.3, y: 3.98, w: 1.18, h: 0.28, fontSize: 9.5, color: C.lighter, align: "center", fontFace: "Calibri", margin: 0 });
      ix += 1.72;
    });
    s.addText("Shubham Gaur  ·  July 2026", { x: 0.7, y: H - 0.55, w: 5, h: 0.3, fontSize: 9.5, color: C.muted, margin: 0 });
  }

  // ===========================================================================
  // 2 — Executive Summary (NEW)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Executive summary");
    title(s, "What This Is, In One Slide");

    bulletList(s, [
      "A meeting-memory system: Gmail, Calendar, and Jira flow in through Airbyte, get turned into a queryable knowledge graph, and feed back into Jira and an autonomous coding agent",
      "Runs entirely on one MacBook — no cloud LLM, no cloud database, no per-token billing after the Airbyte sync",
      "The graph doesn't just store meetings — it computes influence (PageRank), remembers durable facts, decays stale context, recognizes workflows, and answers natural-language questions, grounded in real data",
    ], 0.5, 1.5, 9, 1.7, { fontSize: 11, lineSpacing: 1.4 });

    const stats = [["4", "iterations to get here", C.blue], ["$0", "marginal cost / meeting", C.green], ["126", "automated tests", C.purple], ["12", "Docker/agent services", C.amber]];
    let sx = 0.5;
    const sw = 2.2;
    stats.forEach(([num, label, color]) => {
      card(s, sx, 3.4, sw, 1.35, { fill: C.bgDeep });
      s.addText(num, { x: sx, y: 3.48, w: sw, h: 0.65, fontSize: 27, bold: true, color, align: "center", fontFace: "Cambria", margin: 0 });
      s.addText(label, { x: sx + 0.1, y: 4.12, w: sw - 0.2, h: 0.55, fontSize: 8.5, color: C.lighter, align: "center", fontFace: "Calibri", margin: 0 });
      sx += sw + 0.13;
    });
    footer(s, 2, TOTAL);
  }

  // ===========================================================================
  // 3 — The Problem (business framing)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Why this exists");
    title(s, "The Problem, in Business Terms");

    const problems = [
      ["Lost institutional knowledge", "Decisions and commitments live in scattered docs and Slack threads, then vanish. Nobody can reconstruct who agreed to what.", C.red],
      ["Manual reconciliation tax", "Someone re-reads transcripts to write Jira tickets. Every hour spent here is an hour not spent building.", C.amber],
      ["Cloud LLM spend + exposure", "Every extraction call to a hosted model is a per-token bill AND a copy of your meeting content leaving the building.", C.purple],
    ];
    let px = 0.5;
    problems.forEach(([h, d, color]) => {
      card(s, px, 1.5, 2.9, 3.2, { fill: C.bgDeep, line: color });
      s.addText(h, { x: px + 0.18, y: 1.65, w: 2.55, h: 0.6, fontSize: 12.5, bold: true, color, fontFace: "Calibri", margin: 0 });
      s.addText(d, { x: px + 0.18, y: 2.3, w: 2.55, h: 2.2, fontSize: 9.5, color: C.muted, fontFace: "Calibri", margin: 0, lineSpacingMultiple: 1.35 });
      px += 3.05;
    });
    footer(s, 3, TOTAL);
  }

  // ===========================================================================
  // 4 — Evolution v1 -> v4 (NEW)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "History");
    title(s, "Four Iterations to Get Here");

    const versions = [
      ["v1", "Python + Obsidian vault", "Manual notes, no structure, nothing queryable", C.muted],
      ["v2", "n8n + Confluence + Jira", "Workflow automation, still no graph, no memory", C.blue],
      ["v3", "Airbyte Cloud + Render + Groq + Memgraph Cloud", "First real graph — but 4 paid cloud services, data left the building on every call", C.purple],
      ["v4", "Airbyte Cloud + local Postgres + LM Studio + local Memgraph", "Same ingestion backbone, everything else pulled onto one Mac", C.teal],
    ];
    let y = 1.45;
    versions.forEach(([v, stack, note, color], i) => {
      s.addShape(pres.shapes.OVAL, { x: 0.5, y: y + 0.02, w: 0.5, h: 0.5, fill: { color } });
      s.addText(v, { x: 0.5, y: y + 0.02, w: 0.5, h: 0.5, fontSize: 13, bold: true, color: C.white, align: "center", valign: "middle", fontFace: "Cambria", margin: 0 });
      s.addText(stack, { x: 1.15, y: y - 0.02, w: 7.85, h: 0.32, fontSize: 12, bold: true, color: C.white, fontFace: "Courier New", margin: 0 });
      s.addText(note, { x: 1.15, y: y + 0.3, w: 7.85, h: 0.4, fontSize: 9.3, color: C.muted, fontFace: "Calibri", margin: 0 });
      if (i < 3) s.addShape(pres.shapes.RECTANGLE, { x: 0.74, y: y + 0.52, w: 0.02, h: 0.35, fill: { color: C.muted } });
      y += 0.87;
    });
    s.addText("v3 → v4 is not a rewrite — it's every cloud dependency swapped for a local equivalent, one at a time.", {
      x: 0.5, y: 4.95, w: 9, h: 0.3, fontSize: 9.5, italic: true, color: C.teal, align: "center", fontFace: "Calibri", margin: 0,
    });
    footer(s, 4, TOTAL);
  }

  // ===========================================================================
  // 5 — Why Fully Local — Business Case (NEW)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Business case");
    title(s, "Why Fully Local — What v3 Cost Us");

    card(s, 0.5, 1.45, 4.4, 3.6, { fill: C.bgDeep, line: C.red });
    s.addText("Removed since v3", { x: 0.68, y: 1.57, w: 4, h: 0.28, fontSize: 11, bold: true, color: C.red, fontFace: "Calibri", margin: 0 });
    bulletList(s, [
      "Render.com — paid hosting, gone",
      "Groq API — per-token billing, gone",
      "Memgraph Cloud — managed DB fee, gone",
      "Neon Postgres — managed DB fee, gone",
      "ngrok — tunnel dependency, gone",
      "Ollama — replaced by LM Studio",
      "Slack connector — no signal, cut",
    ], 0.68, 1.95, 4.05, 2.9, { fontSize: 9.3, fontFace: "Courier New", lineSpacing: 1.4 });

    card(s, 5.1, 1.45, 4.4, 3.6, { fill: C.bgDeep, line: C.green });
    s.addText("What that buys", { x: 5.28, y: 1.57, w: 4, h: 0.28, fontSize: 11, bold: true, color: C.green, fontFace: "Calibri", margin: 0 });
    bulletList(s, [
      "Zero marginal cost per extraction — no per-token bill, ever",
      "No meeting content leaves the Mac after the Airbyte sync — nothing to review for a data-processing agreement",
      "No cloud outage takes the pipeline down mid-demo",
      "Airbyte Cloud is the one dependency kept — because it's the right tool for ingestion, not a cost center",
    ], 5.28, 1.95, 4.05, 2.9, { fontSize: 9.5, lineSpacing: 1.4 });

    footer(s, 5, TOTAL);
  }

  // ===========================================================================
  // 6 — Why Airbyte Is Central (business case, audience-specific)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Business case");
    title(s, "Why Airbyte Stays");

    const rows = [
      [icons.plug, "300+ connectors, zero code", "Gmail, Calendar, and Jira are all production connectors — no auth boilerplate, no pagination logic, no schema maintenance to own."],
      [icons.check, "Incremental + Append+Dedup", "Every sync moves only new data. processed_flag gives the graph builder exactly-once semantics on top of that."],
      [icons.bolt, "Webhook-driven, fully automated", "Airbyte fires a webhook on sync completion — the transform service wakes up and the graph updates with zero manual trigger."],
      [icons.branch, "Bidirectional, not just a source", "Airbyte also reads Jira issues back in — the same sync that pulls meetings closes the loop on ticket status."],
    ];
    let y = 1.5;
    rows.forEach(([icon, h, d]) => {
      iconCircle(s, icon, 0.5, y, 0.48, C.card2);
      s.addText(h, { x: 1.2, y: y - 0.02, w: 7.9, h: 0.3, fontSize: 12, bold: true, color: C.white, fontFace: "Calibri", margin: 0 });
      s.addText(d, { x: 1.2, y: y + 0.28, w: 7.9, h: 0.5, fontSize: 9.5, color: C.muted, fontFace: "Calibri", margin: 0 });
      y += 0.87;
    });
    footer(s, 6, TOTAL);
  }

  // ===========================================================================
  // 7 — Full architecture diagram (updated from the v3 one-pager)
  // ===========================================================================
  {
    const s = baseSlide();
    s.addImage({
      path: path.join(ASSETS, "architecture_v4.jpg"),
      x: 0.12, y: 0.08, w: 9.76, h: 5.1,
      sizing: { type: "contain", w: 9.76, h: 5.1 },
    });
    footer(s, 7, TOTAL);
  }

  // ===========================================================================
  // 8 — Architecture with ports & protocols
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "System design");
    title(s, "Architecture — Services & Ports");

    const boxes = [
      { x: 0.5, y: 1.35, w: 1.72, h: 0.85, t: "Postgres\n:5432", c: C.blue },
      { x: 2.37, y: 1.35, w: 1.72, h: 0.85, t: "transform_service\nFastAPI :8000", c: C.purple },
      { x: 4.24, y: 1.35, w: 1.72, h: 0.85, t: "Memgraph\nBolt :7687", c: C.teal },
      { x: 6.11, y: 1.35, w: 1.72, h: 0.85, t: "Lab :3000\nMCP :8001", c: C.amber },
      { x: 7.98, y: 1.35, w: 1.52, h: 0.85, t: "LM Studio\n:1234 (host)", c: C.green },
    ];
    boxes.forEach((b) => {
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: b.x, y: b.y, w: b.w, h: b.h, rectRadius: 0.05, fill: { color: C.card }, line: { color: b.c, width: 1.1 } });
      s.addText(b.t, { x: b.x, y: b.y, w: b.w, h: b.h, fontSize: 9, color: C.white, bold: true, align: "center", valign: "middle", fontFace: "Courier New", margin: 2 });
    });
    [[2.22, 1.775], [4.09, 1.775], [5.96, 1.775], [7.83, 1.775]].forEach(([x, y]) => {
      s.addShape(pres.shapes.RIGHT_ARROW, { x, y: y - 0.08, w: 0.15, h: 0.16, fill: { color: C.muted } });
    });
    s.addText("dev_agent :8002 (own service, polls Jira via REST, opens PRs via GitHub API)", {
      x: 0.5, y: 2.28, w: 9, h: 0.25, fontSize: 8.5, italic: true, color: C.muted, fontFace: "Courier New", margin: 0,
    });

    codeBlock(s, [
      "MEMGRAPH_HOST=memgraph  MEMGRAPH_PORT=7687",
      "LM_STUDIO_BASE_URL=http://host.docker.internal:1234/v1",
      "LM_STUDIO_MODEL=gemma3-12b",
      "LM_STUDIO_EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5",
      "POSTGRES_HOST=postgres  POSTGRES_PORT=5432",
    ], 0.5, 2.65, 4.4, 2.35, { fontSize: 8.6 });

    card(s, 5.1, 2.65, 4.4, 2.35, { fill: C.bgDeep, line: C.blue });
    s.addText("Data flow", { x: 5.28, y: 2.78, w: 4, h: 0.28, fontSize: 10.5, bold: true, color: C.blue, fontFace: "Calibri", margin: 0 });
    bulletList(s, [
      "Airbyte Cloud → Postgres (raw_emails, raw_calendar_events, raw_jira_issues)",
      "APScheduler polls every 5 min + POST /webhook/airbyte on sync complete",
      "graph_builder.py: classify → extract → MERGE Cypher tx → algorithms → memory → Jira push",
      "processed_flag column gives exactly-once semantics per row",
    ], 5.28, 3.1, 4.0, 1.85, { fontSize: 8.8, lineSpacing: 1.3 });

    footer(s, 8, TOTAL);
  }

  // ===========================================================================
  // 9 — Graph Data Model
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Schema");
    title(s, "Graph Data Model");

    card(s, 0.5, 1.4, 4.4, 3.65, { fill: C.bgDeep, line: C.teal });
    s.addText("Node types (11)", { x: 0.68, y: 1.5, w: 4, h: 0.28, fontSize: 11, bold: true, color: C.teal, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "Meeting {id, date, title, kind,",
      "  platform, duration_minutes,",
      "  relevance_weight, embedding[768]}",
      "Person {email, name, pagerank_score,",
      "  community_id, betweenness_centrality}",
      "Topic, Decision, ActionItem, Organization",
      "Fact {id, text, confidence, embedding}",
      "Preference {category, value, confidence}",
      "Procedure, ProcedureStep, MemorySession",
    ], 0.68, 1.85, 4.05, 2.05, { fontSize: 8.3, color: C.light, line: "0D9488" });
    s.addText("All nodes: id (uuid5, deterministic) · created_at · updated_at", {
      x: 0.68, y: 4.05, w: 4.05, h: 0.3, fontSize: 8, italic: true, color: C.muted, fontFace: "Calibri", margin: 0,
    });
    s.addText("Constraints: UNIQUE on Meeting.id, Person.email, Topic.name,\nDecision.id, ActionItem.id, Organization.domain,\nFact.id, Preference.id, Procedure.id, ProcedureStep.id,\nMemorySession.id", {
      x: 0.68, y: 4.42, w: 4.05, h: 0.6, fontSize: 7.8, color: C.muted, fontFace: "Courier New", margin: 0,
    });

    card(s, 5.1, 1.4, 4.4, 3.65, { fill: C.bgDeep, line: C.purple });
    s.addText("Edge types (17)", { x: 5.28, y: 1.5, w: 4, h: 0.28, fontSize: 11, bold: true, color: C.purple, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "ATTENDED {role}   DISCUSSED   PRODUCED",
      "ASSIGNED_TO   WORKS_AT   FOLLOWS_UP",
      "HAS_FACT   PREFERS",
      "KNOWS {weight}   INTERESTED_IN {weight}",
      "PRECEDED_BY {gap_days}",
      "CAUSED_BY {confidence}",
      "FOLLOWS_PROCEDURE {confidence}",
      "HAS_STEP   NEXT_STEP {condition}",
      "ACCESSED",
    ], 5.28, 1.85, 4.05, 2.05, { fontSize: 8.3, color: C.light, line: "5C67F2" });
    s.addText("Writes: MERGE only, never CREATE, for uniquely-keyed nodes.\nMulti-node writes batched in one Cypher transaction (ACID).", {
      x: 5.28, y: 4.05, w: 4.05, h: 0.5, fontSize: 8, italic: true, color: C.muted, fontFace: "Calibri", margin: 0,
    });
    s.addText("KNOWS is stored in one canonical direction\n(lexicographic email order) — query with (a)-[:KNOWS]-(b)", {
      x: 5.28, y: 4.55, w: 4.05, h: 0.5, fontSize: 7.8, color: C.muted, fontFace: "Courier New", margin: 0,
    });

    footer(s, 9, TOTAL);
  }

  // ===========================================================================
  // 10 — Ingestion Pipeline in Code
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "transform_service/");
    title(s, "Ingestion Pipeline — Real Code");

    const mods = [
      ["classifier.py", "classify(text, meta) -> float — rules-based, no LLM. Threshold: score >= 0.40 proceeds.", C.blue],
      ["extractor.py", "extract_meeting() — LM Studio JSON-mode, temperature=0.0, @with_retry(max_attempts=3, base_delay=2.0)", C.purple],
      ["graph_builder.py", "process_email/process_calendar_event — single try block: classify → extract → graph → algorithms → memory → Jira", C.teal],
      ["memgraph_client.py", "The ONLY file with Cypher. upsert_meeting_graph() batches Meeting+Person+Org+Topic+Decision+ActionItem in one tx()", C.amber],
    ];
    let y = 1.5;
    mods.forEach(([h, d, color]) => {
      s.addShape(pres.shapes.OVAL, { x: 0.5, y, w: 0.14, h: 0.14, fill: { color } });
      s.addText(h, { x: 0.78, y: y - 0.08, w: 2.6, h: 0.3, fontSize: 11.5, bold: true, color: C.white, fontFace: "Courier New", margin: 0 });
      s.addText(d, { x: 3.5, y: y - 0.08, w: 6.0, h: 0.5, fontSize: 8.8, color: C.muted, fontFace: "Calibri", margin: 0 });
      y += 0.62;
    });

    codeBlock(s, [
      "MERGE (m:Meeting {id: $id})",
      "ON CREATE SET m.created_at = $now, m.relevance_weight = 1.0",
      "SET m.title = $title, m.kind = $kind, m.embedding = $vec ...",
      "// Person, Org, Topic, Decision, ActionItem MERGEs follow,",
      "// all inside the same tx.run() block — commit or rollback together",
    ], 0.5, 4.05, 9, 1.15, { fontSize: 8.2 });

    footer(s, 10, TOTAL);
  }

  // ===========================================================================
  // 11 — Graph Algorithms, real MAGE calls
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "graph_algorithms.py — only file with MAGE CALL");
    title(s, "Graph Algorithms — Actual MAGE Calls");

    codeBlock(s, [
      "CALL pagerank.get()",
      "  YIELD node, rank SET node.pagerank_score = rank",
      "",
      "CALL community_detection.get()  -- fast path (Louvain)",
      "  YIELD node, community_id SET node.community_id = community_id",
      "",
      "CALL igraphalg.community_leiden()  -- nightly path (accurate)",
      "  YIELD node, community_id SET node.community_id = community_id",
      "",
      "CALL betweenness_centrality.get()",
      "  YIELD node, betweenness_centrality SET node.betweenness_centrality = ...",
      "",
      "CALL degree_centrality.get()",
      "  YIELD node, degree AS degree_centrality SET node.degree_centrality = ...",
      "",
      "CALL weakly_connected_components.get()",
      "  YIELD node, component_id SET node.wcc_id = component_id",
    ], 0.5, 1.4, 5.55, 3.85, { fontSize: 8.4 });

    card(s, 6.2, 1.4, 3.3, 3.85, { fill: C.bgDeep, line: C.amber });
    s.addText("Trigger points", { x: 6.38, y: 1.52, w: 3, h: 0.28, fontSize: 10.5, bold: true, color: C.amber, fontFace: "Calibri", margin: 0 });
    bulletList(s, [
      "Fast path — after every processed meeting, in graph_builder.py's try block",
      "Full path — nightly cron, 02:00, via APScheduler in main.py lifespan",
      "Each CALL wrapped in its own try/except — one algorithm failing never aborts the others",
      "result.consume() called explicitly — the async driver defers execution otherwise, misattributing errors to the next statement",
    ], 6.38, 1.88, 3.0, 3.3, { fontSize: 8.4, lineSpacing: 1.3 });

    footer(s, 11, TOTAL);
  }

  // ===========================================================================
  // 12 — Graph Algorithms, LIVE RESULTS (NEW)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Not a mockup — real output, this session");
    title(s, "Graph Algorithms — Live Results");

    card(s, 0.5, 1.4, 4.4, 3.8, { fill: C.bgDeep, line: C.teal });
    s.addText("PageRank leaderboard", { x: 0.68, y: 1.52, w: 4, h: 0.28, fontSize: 11, bold: true, color: C.teal, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "person            pagerank  community",
      "Femi Oduwole      0.0047    11",
      "Matteo Vaiente    0.0045    4",
      "Matteo            0.0045    4",
      "Femi Oduwole      0.0037    4",
      "Mark Johnston     0.0037    4",
      "Phil Loranger     0.0037    4",
      "Jacob Barka       0.0037    11",
    ], 0.68, 1.88, 4.05, 2.0, { fontSize: 8.4 });
    s.addText("MATCH (p:Person) WHERE p.pagerank_score IS NOT NULL\nRETURN p.name, p.pagerank_score ORDER BY ... DESC", {
      x: 0.68, y: 4.0, w: 4.05, h: 0.5, fontSize: 7.6, italic: true, color: C.muted, fontFace: "Courier New", margin: 0,
    });
    s.addText("Femi Oduwole appears twice — same person, two attendee records from different meetings, not deduplicated on name. Real data, real rough edge.", {
      x: 0.68, y: 4.55, w: 4.05, h: 0.55, fontSize: 7.8, color: C.muted, fontFace: "Calibri", margin: 0,
    });

    card(s, 5.1, 1.4, 4.4, 3.8, { fill: C.bgDeep, line: C.purple });
    s.addText("Community detection", { x: 5.28, y: 1.52, w: 4, h: 0.28, fontSize: 11, bold: true, color: C.purple, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "community  members",
      "5          42",
      "4          22",
      "11         17",
      "-1         11",
      "9          9",
      "6          6",
    ], 5.28, 1.88, 4.05, 1.75, { fontSize: 8.4 });
    s.addText("Real clusters, not one blob or all-singletons — Louvain found actual structure in 74 real meetings.", {
      x: 5.28, y: 3.75, w: 4.05, h: 0.5, fontSize: 8, italic: true, color: C.muted, fontFace: "Calibri", margin: 0,
    });
    s.addText("This ran live in this session via graph_algorithms.run_fast_algorithms() — the exact function graph_builder.py calls after every real meeting.", {
      x: 5.28, y: 4.3, w: 4.05, h: 0.75, fontSize: 8, color: C.muted, fontFace: "Calibri", margin: 0,
    });

    footer(s, 12, TOTAL);
  }

  // ===========================================================================
  // 13 — Memory Modules, the math
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "semantic_memory.py · episodic_memory.py");
    title(s, "Memory — The Actual Formulas");

    card(s, 0.5, 1.4, 4.4, 3.8, { fill: C.bgDeep, line: C.teal });
    s.addText("Fact confidence", { x: 0.68, y: 1.52, w: 4, h: 0.28, fontSize: 11, bold: true, color: C.teal, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "ON CREATE  f.confidence = 0.3",
      "ON MATCH   f.confidence = min(1.0,",
      "             f.confidence + 0.1)",
      "",
      "-- nightly consolidation:",
      "WHERE f.source_count % 3 = 0",
      "  SET f.confidence = min(1.0,",
      "        f.confidence + 0.2)",
    ], 0.68, 1.88, 4.05, 1.75, { fontSize: 8.4 });
    s.addText("KNOWS / INTERESTED_IN: ON MATCH SET weight = weight + 1, pure Cypher, no LLM call", {
      x: 0.68, y: 3.75, w: 4.05, h: 0.55, fontSize: 8.3, color: C.muted, fontFace: "Calibri", margin: 0,
    });
    s.addText("Preference: starts 0.5, same +0.1 reconfirm rule", {
      x: 0.68, y: 4.32, w: 4.05, h: 0.35, fontSize: 8.3, color: C.muted, fontFace: "Calibri", margin: 0,
    });

    card(s, 5.1, 1.4, 4.4, 3.8, { fill: C.bgDeep, line: C.blue });
    s.addText("Relevance decay", { x: 5.28, y: 1.52, w: 4, h: 0.28, fontSize: 11, bold: true, color: C.blue, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "-- nightly, decay_relevance():",
      "SET m.relevance_weight = CASE",
      "  WHEN relevance_weight IS NULL",
      "    THEN 1.0",
      "  WHEN relevance_weight <= 0.1",
      "    THEN 0.1  -- floor",
      "  ELSE relevance_weight * 0.95",
      "END",
    ], 5.28, 1.88, 4.05, 1.75, { fontSize: 8.4 });
    s.addText("PRECEDED_BY.gap_days = (date($meeting_date) - date(prior.date)).day\n(Memgraph has no duration.inDays — use date subtraction)", {
      x: 5.28, y: 3.75, w: 4.05, h: 0.6, fontSize: 8.3, color: C.muted, fontFace: "Calibri", margin: 0,
    });
    s.addText("CAUSED_BY: only when follow_up_needed=true, capped at 1 link/meeting, >50% word overlap required", {
      x: 5.28, y: 4.35, w: 4.05, h: 0.5, fontSize: 8.3, color: C.muted, fontFace: "Calibri", margin: 0,
    });

    footer(s, 13, TOTAL);
  }

  // ===========================================================================
  // 13 — Vector Search, real Cypher
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "vector_memory.py");
    title(s, "Vector Search — Real Cypher");

    codeBlock(s, [
      "CREATE VECTOR INDEX meeting_embedding_idx",
      "  ON :Meeting(embedding)",
      "  WITH CONFIG {\"dimension\": 768,",
      "               \"capacity\": 2048,",
      "               \"metric\": \"cos\"}",
      "",
      "CALL vector_search.search(",
      "  $index_name, $limit, $query_vector)",
      "YIELD node, similarity, distance",
      "RETURN node.id, similarity",
      "ORDER BY similarity DESC",
    ], 0.5, 1.4, 4.85, 3.85, { fontSize: 8.6 });

    card(s, 5.55, 1.4, 3.95, 3.85, { fill: C.bgDeep, line: C.green });
    s.addText("Pipeline", { x: 5.73, y: 1.52, w: 3.6, h: 0.28, fontSize: 10.5, bold: true, color: C.green, fontFace: "Calibri", margin: 0 });
    bulletList(s, [
      "embed_text() → LM Studio /v1/embeddings, text-embedding-nomic-embed-text-v1.5, 768-dim",
      "embed_meeting() writes Meeting.embedding on ingestion",
      "embed_facts_for_meeting() embeds only Facts missing embeddings — idempotent",
      "graph_algorithms.vector_search() is the only place CALL vector_search.* appears",
      "backfill_embeddings() — one-off, for pre-existing data",
    ], 5.73, 1.88, 3.6, 3.3, { fontSize: 8.6, lineSpacing: 1.32 });

    footer(s, 14, TOTAL);
  }

  // ===========================================================================
  // 14 — Autonomous Dev Agent — state machine
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "dev_agent/ — separate Docker service, :8002");
    title(s, "Dev Agent — State Machine");

    const stages = [
      ["BACKLOG", "description != ''\nno skip label", C.muted],
      ["TO DO", "autonomous\ntriage", C.blue],
      ["IN PROGRESS", "git worktree\nagent/<KEY>", C.purple],
      ["IN REVIEW", "PR opened\nGitHub verified", C.amber],
      ["MERGED", "human\ncheckpoint", C.green],
    ];
    let sx = 0.5;
    const sw = 1.6;
    stages.forEach(([h, d, color], i) => {
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: sx, y: 1.5, w: sw, h: 1.05, rectRadius: 0.05, fill: { color: C.card }, line: { color, width: 1.1 } });
      s.addText(h, { x: sx, y: 1.58, w: sw, h: 0.28, fontSize: 9.5, bold: true, color, align: "center", fontFace: "Calibri", margin: 0 });
      s.addText(d, { x: sx + 0.06, y: 1.9, w: sw - 0.12, h: 0.55, fontSize: 7.5, color: C.muted, align: "center", fontFace: "Courier New", margin: 0 });
      if (i < 4) s.addShape(pres.shapes.RIGHT_ARROW, { x: sx + sw + 0.02, y: 1.95, w: 0.11, h: 0.14, fill: { color: C.muted } });
      sx += sw + 0.15;
    });

    codeBlock(s, [
      "ANTHROPIC_API_KEY=\"\"           # LM Studio only, never api.anthropic.com",
      "LM_STUDIO_ANTHROPIC_URL=http://host.docker.internal:1234",
      "DEV_AGENT_MAX_ATTEMPTS=1       DEV_AGENT_MAX_TURNS=40",
      "DEV_AGENT_TIMEOUT_SECONDS=1800 DEV_AGENT_POLL_MINUTES=10",
    ], 0.5, 2.75, 9, 1.05, { fontSize: 8.3, color: C.amber });

    card(s, 0.5, 3.95, 9, 1.15, { fill: C.bgDeep });
    bulletList(s, [
      "PR verification is independent — GitHub API confirms the PR exists, never trusts the agent's own claim",
      "Failure path: ticket returns to TO DO (not stuck in IN PROGRESS), Jira comment posted, dev_agent_runs.status='failed'",
      "Auto-merge deliberately NOT implemented — human review is the one remaining checkpoint",
    ], 0.68, 4.08, 8.65, 1.0, { fontSize: 8.6, fontFace: "Courier New", lineSpacing: 1.28 });

    footer(s, 15, TOTAL);
  }

  // ===========================================================================
  // 16 — Action Agent, what it solves (NEW)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "The gap dev_agent doesn't cover");
    title(s, "Action Agent — What It Solves");

    bulletList(s, [
      "dev_agent only works engineering tickets — real code changes, a real PR. Every meeting also produces action items that aren't code: \"follow up with Matteo\", \"share the webhook docs\", \"review the budget adjustments\"",
      "Those got pushed to Jira by jira_pusher.py, labeled meeting-action-item, and then — nothing. dev_agent correctly skips them. Nobody else was working them.",
      "action_agent.py closes that gap: it picks up exactly the tickets dev_agent is designed to ignore, drafts the real deliverable grounded in graph context, and hands it to a human to approve",
    ], 0.5, 1.5, 9, 2.0, { fontSize: 12, lineSpacing: 1.5 });

    card(s, 0.5, 3.65, 9, 1.35, { fill: C.bgDeep, line: C.purple });
    s.addText("Two agents, zero overlap", { x: 0.68, y: 3.78, w: 8.6, h: 0.28, fontSize: 11, bold: true, color: C.purple, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "dev_agent        engineering tickets   -> git worktree, PR, IN REVIEW",
      "action_agent     everything else       -> drafted comment, IN REVIEW",
    ], 0.68, 4.1, 8.65, 0.75, { fontSize: 9.5 });

    footer(s, 16, TOTAL);
  }

  // ===========================================================================
  // 17 — Action Agent, the pipeline (NEW)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "transform_service/action_agent.py — the only file using airbyte-agent-sdk");
    title(s, "Action Agent — Pipeline");

    const steps = [
      ["FIND", "To Do +\nmeeting-action-item"],
      ["GUARD", "marker comment?\nskip to transition"],
      ["CONTEXT", "memory_retrieval\n.full_memory_query()"],
      ["DRAFT", "LM Studio\n(local, temp=0.0)"],
      ["COMMENT", "post_draft()\nADF + marker"],
      ["TRANSITION", "To Do ->\nIn Review"],
    ];
    let sx = 0.5;
    const sw = 1.42;
    steps.forEach(([h, d], i) => {
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: sx, y: 1.5, w: sw, h: 1.15, rectRadius: 0.05, fill: { color: C.card }, line: { color: C.purple, width: 1.1 } });
      s.addText(h, { x: sx, y: 1.6, w: sw, h: 0.28, fontSize: 9, bold: true, color: C.purple, align: "center", fontFace: "Calibri", margin: 0 });
      s.addText(d, { x: sx + 0.05, y: 1.92, w: sw - 0.1, h: 0.65, fontSize: 7.2, color: C.muted, align: "center", fontFace: "Courier New", margin: 0 });
      if (i < 5) s.addShape(pres.shapes.RIGHT_ARROW, { x: sx + sw + 0.01, y: 1.98, w: 0.1, h: 0.14, fill: { color: C.muted } });
      sx += sw + 0.11;
    });

    card(s, 0.5, 2.85, 9, 1.05, { fill: C.bgDeep, line: C.green });
    s.addText("Airbyte Agents SDK is a tool layer, not a hosted brain", { x: 0.68, y: 2.96, w: 8.6, h: 0.26, fontSize: 10.5, bold: true, color: C.green, fontFace: "Calibri", margin: 0 });
    s.addText("BYO-LLM by design — the SDK gives typed Jira operations (search, comment, transition); LM Studio does 100% of the reasoning. No exception to the no-cloud-LLM rule.", {
      x: 0.68, y: 3.24, w: 8.6, h: 0.55, fontSize: 8.8, color: C.lighter, fontFace: "Calibri", margin: 0,
    });

    card(s, 0.5, 4.05, 9, 1.05, { fill: C.bgDeep });
    bulletList(s, [
      "Idempotency: ACTION_AGENT_MARKER in the comment body gates retries — a partial failure (comment posted, transition didn't) re-enters at TRANSITION, never double-drafts",
      "AIRBYTE_AGENTS_CLIENT_ID/SECRET are deliberately separate from AIRBYTE_CLIENT_ID/SECRET (Airbyte Cloud ELT) — different product, credentials never conflated",
    ], 0.68, 4.18, 8.65, 0.9, { fontSize: 8.6, lineSpacing: 1.3 });

    footer(s, 17, TOTAL);
  }

  // ===========================================================================
  // 18 — Built with rigor: the bug live testing found (NEW)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Task-level review approved every task. Live testing didn't lie.");
    title(s, "Built With Rigor, Not Just Built");

    const steps = [
      ["6 tasks, 6 reviews", "Every task passed spec + quality review against mocked tests", C.blue],
      ["Live run: instant failure", "'Issue' object has no attribute 'get' — first real Jira call, first try", C.red],
      ["Root cause found", "The SDK returns typed Pydantic models. Dicts AND MagicMock both silently support .get() — every mock hid the exact mismatch", C.amber],
      ["Fixtures rebuilt, fixed for real", "Tests now construct real SDK model instances. 126/126 green, then a live run against the real board succeeded", C.green],
    ];
    let y = 1.42;
    steps.forEach(([h, d, color]) => {
      s.addShape(pres.shapes.OVAL, { x: 0.5, y: y + 0.04, w: 0.15, h: 0.15, fill: { color } });
      s.addText(h, { x: 0.8, y: y - 0.06, w: 8.3, h: 0.28, fontSize: 11.5, bold: true, color: C.white, fontFace: "Calibri", margin: 0 });
      s.addText(d, { x: 0.8, y: y + 0.22, w: 8.3, h: 0.4, fontSize: 8.8, color: C.muted, fontFace: "Calibri", margin: 0 });
      y += 0.66;
    });

    codeBlock(s, [
      "AttributeError: 'Issue' object has no attribute 'get'",
      "  # find_eligible_tickets: rec.get(\"fields\")   -> rec.fields",
      "  # has_agent_draft:      comment.get(\"body\")  -> getattr(comment, \"body\", None)",
      "  # transition_to_review: t.get(\"to\")          -> getattr(t, \"to\", None)",
    ], 0.5, 4.18, 9, 1.0, { fontSize: 7.6 });

    footer(s, 18, TOTAL);
  }

  // ===========================================================================
  // 19 — Live Proof: Jira (NEW)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Real ticket, real board — shubhamgaur1.atlassian.net");
    title(s, "Live Proof — Jira");

    card(s, 0.5, 1.35, 5.15, 3.75, { fill: "FFFFFF", line: "E2E8F0" });
    s.addImage({
      path: path.join(ASSETS, "jira_scrum47_action_agent.jpg"),
      x: 0.6, y: 1.44, w: 4.95, h: 3.57, sizing: { type: "contain", w: 4.95, h: 3.57 },
    });

    card(s, 5.85, 1.35, 3.65, 1.85, { fill: C.bgDeep, line: C.green });
    s.addText("SCRUM-47 — before -> after", { x: 6.03, y: 1.47, w: 3.3, h: 0.26, fontSize: 10.5, bold: true, color: C.green, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "To Do  ->  In Review",
      "labels: meeting-action-item",
      "+1 comment: [action-agent draft]",
    ], 6.03, 1.78, 3.3, 1.3, { fontSize: 8.4 });

    card(s, 5.85, 3.32, 3.65, 1.78, { fill: C.bgDeep, line: C.blue });
    s.addText("What the agent actually wrote", { x: 6.03, y: 3.44, w: 3.3, h: 0.26, fontSize: 10.5, bold: true, color: C.blue, fontFace: "Calibri", margin: 0 });
    s.addText("“Reach out to Sarah Chen and David Lee who expressed interest in advanced features during the workshop on 2024-04-14.”", {
      x: 6.03, y: 3.74, w: 3.3, h: 0.95, fontSize: 8.6, italic: true, color: C.lighter, fontFace: "Calibri", margin: 0, lineSpacingMultiple: 1.25,
    });
    s.addText("Real names, real date — from 2 graph nodes consulted, not generic filler", {
      x: 6.03, y: 4.72, w: 3.3, h: 0.35, fontSize: 7.6, color: C.muted, fontFace: "Calibri", margin: 0,
    });

    footer(s, 19, TOTAL);
  }

  // ===========================================================================
  // 20 — Live Proof: Airbyte Agents dashboard (NEW)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "app.airbyte.ai — org Onix, Tool Calls (not Sessions)");
    title(s, "Live Proof — Airbyte Agents Dashboard");

    card(s, 0.5, 1.35, 5.6, 3.75, { fill: "FFFFFF", line: "E2E8F0" });
    s.addImage({
      path: path.join(ASSETS, "airbyte_agents_tool_calls.jpg"),
      x: 0.6, y: 1.44, w: 5.4, h: 3.57, sizing: { type: "contain", w: 5.4, h: 3.57 },
    });

    card(s, 6.3, 1.35, 3.2, 3.75, { fill: C.bgDeep, line: C.purple });
    s.addText("Every SDK call, logged by Airbyte", { x: 6.48, y: 1.47, w: 2.85, h: 0.5, fontSize: 10.5, bold: true, color: C.purple, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "issues / api_search",
      "issue_comments / list",
      "issue_comments / create",
      "issue_transitions / list",
      "issue_transitions / create",
    ], 6.48, 2.0, 2.85, 1.55, { fontSize: 8 });
    s.addText("“Sessions” tracks Airbyte's own web-chat UI — a different feature. “Tool Calls” is what our SDK code triggers, and it's exactly what we wanted to see.", {
      x: 6.48, y: 3.65, w: 2.85, h: 1.3, fontSize: 8.2, color: C.lighter, fontFace: "Calibri", margin: 0, lineSpacingMultiple: 1.3,
    });

    footer(s, 20, TOTAL);
  }

  // ===========================================================================
  // 21 — Bidirectional Jira + MCP
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "jira_pusher.py · jira_agent.py · action_agent.py · MCP servers");
    title(s, "Bidirectional Jira, One-Turn Agent Access");

    card(s, 0.5, 1.4, 4.4, 3.55, { fill: C.bgDeep, line: C.purple });
    s.addText("Jira loop — three components now", { x: 0.68, y: 1.52, w: 4.0, h: 0.28, fontSize: 11, bold: true, color: C.purple, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "jira_pusher.py    ActionItem -> Jira issue",
      "Airbyte Jira src  -> raw_jira_issues (Postgres)",
      "jira_agent.py     issue -> ActionItem.jira_status",
      "action_agent.py   drafts + resolves the rest",
    ], 0.68, 1.88, 4.05, 1.5, { fontSize: 8.2, color: C.light, line: "5C67F2" });
    s.addText("All Jira REST calls live in jira_client.py — no exceptions. action_agent.py is the only one using the Airbyte Agent SDK instead.", {
      x: 0.68, y: 3.5, w: 4.05, h: 1.3, fontSize: 8, italic: true, color: C.muted, fontFace: "Calibri", margin: 0, lineSpacingMultiple: 1.3,
    });

    card(s, 5.1, 1.4, 4.4, 3.55, { fill: C.bgDeep, line: C.blue });
    s.addText("MCP tool surface", { x: 5.28, y: 1.52, w: 3.6, h: 0.28, fontSize: 11, bold: true, color: C.blue, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "memgraph MCP  (MCP_READ_ONLY=false)",
      "  run_query, get_schema,",
      "  get_node_neighborhood,",
      "  search_node_vectors, get_page_rank",
      "",
      "jira MCP",
      "  jira_get, jira_post,",
      "  jira_put, jira_patch, jira_delete",
    ], 5.28, 1.88, 4.05, 2.4, { fontSize: 8.2, color: C.light, line: "0EA5E9" });
    s.addText("One conversation, both MCPs — Claude Desktop hits Memgraph + Jira in the same turn", {
      x: 5.28, y: 4.35, w: 4.05, h: 0.5, fontSize: 8, italic: true, color: C.muted, fontFace: "Calibri", margin: 0,
    });

    footer(s, 21, TOTAL);
  }

  // ===========================================================================
  // 22 — Module Boundaries & Absolute Rules
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "CLAUDE.md — enforced by convention, not a linter");
    title(s, "Module Boundaries & Absolute Rules");

    const rules = [
      "graph_algorithms.py is the ONLY file with MAGE CALL procedures",
      "memgraph_client.py is the ONLY file with Cypher (plus the memory modules for their own node types)",
      "db.py is the ONLY file with SQL",
      "jira_client.py is the ONLY file with Jira REST calls (dev_agent and transform_service both use it)",
      "action_agent.py is the ONLY file using airbyte-agent-sdk",
      "action_agent.py must NEVER be called from graph_builder.py — memory_retrieval is query-time only",
      "All Cypher writes: MERGE, never CREATE, for uniquely-keyed nodes",
      "Multi-node writes: single Cypher transaction, never sequential driver calls",
      "httpx.AsyncClient only — never synchronous requests",
      "@with_retry(max_attempts=3, base_delay=2.0) on all external calls",
      "dev_agent never defaults is_engineering_task to True when missing — fail safe",
      "Auto-merge is not implemented — do not add it without explicit go-ahead",
    ];
    const half = Math.ceil(rules.length / 2);
    [rules.slice(0, half), rules.slice(half)].forEach((col, ci) => {
      const cx = 0.5 + ci * 4.65;
      card(s, cx, 1.4, 4.4, 3.8, { fill: C.bgDeep, line: ci === 0 ? C.red : C.amber });
      s.addText(col.map((t) => ({ text: t, options: { bullet: { code: "2022" }, breakLine: true } })), {
        x: cx + 0.2, y: 1.6, w: 4.0, h: 3.4, fontSize: 9.2, color: C.light, fontFace: "Courier New",
        margin: 0, valign: "top", lineSpacingMultiple: 1.45,
      });
    });
    footer(s, 22, TOTAL);
  }

  // ===========================================================================
  // 23 — Testing & Ops (NEW)
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Quality & operability");
    title(s, "Testing & Ops");

    card(s, 0.5, 1.4, 4.4, 3.8, { fill: C.bgDeep, line: C.purple });
    iconCircle(s, icons.flask, 0.68, 1.55, 0.42, C.purple);
    s.addText("Testing", { x: 1.22, y: 1.62, w: 3.5, h: 0.3, fontSize: 12, bold: true, color: C.white, fontFace: "Calibri", margin: 0 });
    bulletList(s, [
      "126 automated tests across 12 files, one per build phase (phase14 → phase27)",
      "pytest + pytest-anyio, mocked Memgraph driver and LM Studio client — no live services needed to run the suite",
      "Every new memory/algorithm module shipped with its own test file the same session it was written",
      "make test runs the full suite inside the transform_service container",
    ], 0.68, 2.05, 4.05, 3.05, { fontSize: 9, lineSpacing: 1.35 });

    card(s, 5.1, 1.4, 4.4, 3.8, { fill: C.bgDeep, line: C.amber });
    iconCircle(s, icons.tools, 5.28, 1.55, 0.42, C.amber);
    s.addText("Ops surface (Makefile)", { x: 5.82, y: 1.62, w: 3.6, h: 0.3, fontSize: 12, bold: true, color: C.white, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "make up / down / logs / shell",
      "make test          # full pytest suite",
      "make setup-memgraph  # idempotent schema",
      "make trigger       # fire pipeline manually",
      "make backfill      # reprocess raw rows",
      "make health        # 3-service status check",
      "make dev-agent-trigger / -triage / -runs",
    ], 5.28, 2.05, 4.05, 3.0, { fontSize: 8.4 });

    footer(s, 23, TOTAL);
  }

  // ===========================================================================
  // 24 — Live Proof
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Not a mockup");
    title(s, "This Is the Live Graph");

    card(s, 0.5, 1.35, 4.35, 3.6, { fill: "FFFFFF", line: "E2E8F0" });
    s.addImage({
      path: path.join(ASSETS, "memgraph_02_discussed_knowledge_graph.jpg"),
      x: 0.6, y: 1.44, w: 4.15, h: 3.42, sizing: { type: "contain", w: 4.15, h: 3.42 },
    });
    s.addText("MATCH (m:Meeting)-[r:DISCUSSED]->(t:Topic) RETURN m, r, t — 162 edges", {
      x: 0.5, y: 4.98, w: 4.35, h: 0.25, fontSize: 7.2, italic: true, color: C.muted, align: "center", fontFace: "Courier New", margin: 0,
    });

    card(s, 5.05, 1.35, 4.45, 1.65, { fill: C.bgDeep, line: C.green });
    s.addText('"Who is responsible for testing automation?"', {
      x: 5.23, y: 1.48, w: 4.1, h: 0.5, fontSize: 11.5, bold: true, italic: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addShape(pres.shapes.DOWN_ARROW, { x: 7.15, y: 2.05, w: 0.32, h: 0.18, fill: { color: C.green } });
    s.addText([
      { text: '"Femi leads the QA automation initiative"\n', options: { bold: true, italic: true, color: C.white, fontSize: 10.5, breakLine: true } },
      { text: "similarity = 0.649, cosine metric, zero keyword overlap", options: { color: C.green, fontSize: 8.3, fontFace: "Courier New" } },
    ], { x: 5.23, y: 2.3, w: 4.1, h: 0.65, fontFace: "Calibri", margin: 0, lineSpacingMultiple: 1.2 });

    card(s, 5.05, 3.15, 4.45, 1.8, { fill: C.bgDeep, line: C.blue });
    s.addText("Live numbers, this session", { x: 5.23, y: 3.26, w: 4.0, h: 0.26, fontSize: 9.5, bold: true, color: C.blue, fontFace: "Calibri", margin: 0 });
    codeBlock(s, [
      "126/126 tests passing",
      "266 edges, 13 relationship types",
      "74 meetings · 43 topics · 7 people",
      "vector index size: 74 + 5 (Meeting+Fact)",
    ], 5.23, 3.58, 4.1, 1.3, { fontSize: 8, color: C.light });

    footer(s, 24, TOTAL);
  }

  // ===========================================================================
  // 25 — Live Status / API surface
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Live status");
    title(s, "What's Working Right Now");

    const stats = [["126", "tests passing", C.green], ["74", "meetings", C.blue], ["266", "edges", C.purple], ["768", "embed dims", C.amber]];
    let sx = 0.5;
    const sw = 2.2;
    stats.forEach(([num, label, color]) => {
      card(s, sx, 1.4, sw, 1.2, { fill: C.bgDeep });
      s.addText(num, { x: sx, y: 1.46, w: sw, h: 0.6, fontSize: 26, bold: true, color, align: "center", fontFace: "Cambria", margin: 0 });
      s.addText(label, { x: sx + 0.1, y: 2.04, w: sw - 0.2, h: 0.4, fontSize: 8.5, color: C.lighter, align: "center", fontFace: "Calibri", margin: 0 });
      sx += sw + 0.13;
    });

    codeBlock(s, [
      "GET  /graph/insights/influential | /communities | /bridges",
      "GET  /graph/search/meetings?q=  |  /graph/search/facts?q=",
      "POST /graph/memory/query  {\"question\": str}",
      "GET  /graph/memory/person/{email}  |  /graph/procedures",
      "POST /webhook/airbyte  —  triggers full pipeline end to end",
    ], 0.5, 2.8, 9, 1.3, { fontSize: 8.6, color: C.light });

    card(s, 0.5, 4.28, 9, 0.85, { fill: C.card2 });
    s.addText("docker compose up  —  that's the entire deployment.", {
      x: 0.5, y: 4.28, w: 9, h: 0.85, fontSize: 13, bold: true, color: C.teal, align: "center", valign: "middle", fontFace: "Calibri", margin: 0,
    });
    footer(s, 25, TOTAL);
  }

  // ===========================================================================
  // 26 — What This Demonstrates
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Closing");
    title(s, "What This Demonstrates");

    const rows = [
      [icons.dollar, "A cost story, not just a tech demo", "Four cloud services removed since v3 — zero marginal cost per meeting processed"],
      [icons.database, "Airbyte as the backbone — twice over", "Airbyte Cloud does the ELT; Airbyte Agents gives action_agent its Jira tool layer. Two real products, both kept on purpose"],
      [icons.project, "Graph-native, self-aware memory", "Not just storage — the graph ranks, remembers, decays, and reasons about itself"],
      [icons.robot, "Three autonomous systems, one philosophy", "dev_agent (code), action_agent (everything else), memory_retrieval (Q&A) — all autonomous up to a human checkpoint, never past it"],
    ];
    let y = 1.45;
    rows.forEach(([icon, h, d]) => {
      iconCircle(s, icon, 0.5, y, 0.46, C.card2);
      s.addText(h, { x: 1.18, y: y - 0.02, w: 7.9, h: 0.3, fontSize: 12, bold: true, color: C.white, fontFace: "Calibri", margin: 0 });
      s.addText(d, { x: 1.18, y: y + 0.27, w: 7.9, h: 0.42, fontSize: 9.3, color: C.muted, fontFace: "Calibri", margin: 0 });
      y += 0.8;
    });
    footer(s, 26, TOTAL);
  }

  // ===========================================================================
  // 27 — Extensibility + Closing
  // ===========================================================================
  {
    const s = baseSlide();
    kicker(s, "Where this can go");
    title(s, "Built to Extend, Not Just to Demo");

    bulletList(s, [
      "More Airbyte sources plug into the same classify → extract → graph flow with no architecture change",
      "graph_algorithms.py, memory modules, and vector search are all additive layers on the same node/edge schema — nothing upstream was rewritten to add them",
      "The MCP surface means any future agent gets read+write graph access on day one, not as a follow-up integration",
    ], 0.5, 1.45, 9, 1.6, { fontSize: 11, lineSpacing: 1.4 });

    card(s, 0.5, 3.3, 9, 1.15, { fill: C.card2 });
    s.addText("docker compose up  —  that's the entire deployment.", {
      x: 0.5, y: 3.3, w: 9, h: 0.6, fontSize: 15, bold: true, color: C.teal, align: "center", valign: "middle", fontFace: "Calibri", margin: 0,
    });
    s.addText("Questions?", { x: 0.5, y: 3.85, w: 9, h: 0.5, fontSize: 12, color: C.lighter, align: "center", fontFace: "Calibri", margin: 0 });

    s.addText("Shubham Gaur  ·  meeting-memory-v4", { x: 0.5, y: 4.9, w: 9, h: 0.3, fontSize: 9.5, color: C.muted, align: "center", margin: 0 });
    footer(s, 27, TOTAL);
  }

  const outPath = path.join(ASSETS, "Meeting_Memory_v4_Overview.pptx");
  await pres.writeFile({ fileName: outPath });
  console.log("Wrote " + outPath);
}

main().catch((e) => { console.error(e); process.exit(1); });
