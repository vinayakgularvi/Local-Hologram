<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";

const loading = ref(true);
const err = ref("");
const summary = ref(null);
const recent = ref([]);
const resetSecret = ref("");
const resetMsg = ref("");
const streamState = ref("connecting");
const lastUpdatedAt = ref("");
let es = null;

function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  const base = import.meta.env.VITE_API_BASE;
  if (base) return `${String(base).replace(/\/$/, "")}${p}`;
  if (typeof window !== "undefined") {
    const port = window.location.port;
    const host = window.location.hostname;
    if (port === "5173" || port === "4173") {
      return `http://${host}:8080${p}`;
    }
  }
  return p;
}

function applySnapshot(payload) {
  summary.value = payload.summary || null;
  recent.value = Array.isArray(payload.recent) ? payload.recent : [];
  lastUpdatedAt.value = new Date().toISOString();
  err.value = "";
  loading.value = false;
}

async function loadBootstrap() {
  loading.value = true;
  try {
    const [s, r] = await Promise.all([
      fetch(apiUrl("/api/analytics/summary")),
      fetch(apiUrl("/api/analytics/voice-turns?limit=40")),
    ]);
    if (!s.ok) throw new Error(await s.text());
    if (!r.ok) throw new Error(await r.text());
    const sum = await s.json();
    const rows = await r.json();
    applySnapshot({ summary: sum, recent: rows.items || [] });
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
    loading.value = false;
  }
}

function startStream() {
  if (es) es.close();
  streamState.value = "connecting";
  es = new EventSource(apiUrl("/api/analytics/stream"));
  es.onopen = () => {
    streamState.value = "live";
  };
  es.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.type === "snapshot") {
        applySnapshot(data);
      }
    } catch {
      // ignore malformed chunks
    }
  };
  es.onerror = () => {
    streamState.value = "reconnecting";
  };
}

function stopStream() {
  if (es) {
    es.close();
    es = null;
  }
}

onMounted(async () => {
  await loadBootstrap();
  startStream();
});

onUnmounted(() => {
  stopStream();
});

function fmtMs(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return `${Number(v).toFixed(1)} ms`;
}

function fmtNum(v, d = 0) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return Number(v).toLocaleString(undefined, {
    maximumFractionDigits: d,
    minimumFractionDigits: d,
  });
}

function fmtTs(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

/** Voice turns from API are newest-first (id DESC); charts and table keep latest at top. */
function latestChartRows(rows, limit = 24, predicate = () => true) {
  const out = [];
  for (const r of rows) {
    if (!predicate(r)) continue;
    out.push(r);
    if (out.length >= limit) break;
  }
  return out;
}

/** Transcribe + per-sentence RAG / TTS (elapsed_ms) / humanaudiowithpath only. */
const PIPELINE_STEPS = [
  {
    id: "stt",
    step: 1,
    title: "Transcribe",
    sub: "POST /api/transcribe (final transcript)",
    field: "stt_latency_ms",
    summaryField: "avg_stt_latency_ms",
    track: "stt",
    empty: "Complete a spoken turn with server transcribe enabled.",
  },
  {
    id: "rag-sentence",
    step: 2,
    title: "RAG sentence",
    sub: "RAG stream start → each sentence ready (per turn avg)",
    field: "stream_avg_rag_sentence_ms",
    summaryField: "avg_stream_avg_rag_sentence_ms",
    track: "rag",
    empty: "Voice-stream with VOICE_STREAM_HUMAN_UNIT=sentence.",
  },
  {
    id: "tts-sentence",
    step: 3,
    title: "TTS sentence",
    sub: "/v1/tts/reference elapsed_ms (per turn avg)",
    field: "stream_avg_tts_ms",
    summaryField: "avg_stream_avg_tts_ms",
    track: "stream-tts",
    highlight: true,
    empty: "Voice-stream with VOICE_DISPATCH_MODE=humanaudio.",
  },
  {
    id: "ha-sentence",
    step: 4,
    title: "humanaudiowithpath",
    sub: "POST /humanaudiowithpath per sentence (per turn avg)",
    field: "stream_avg_humanaudio_ms",
    summaryField: "avg_stream_avg_humanaudio_ms",
    track: "humanaudio",
    highlight: true,
    empty: "Voice-stream with VOICE_DISPATCH_MODE=humanaudio.",
  },
];

function summaryAvgKey(step) {
  return step.summaryField || `avg_${step.field}`;
}

function barsForField(field, limit = 16) {
  const rows = latestChartRows(
    recent.value,
    limit,
    (r) => r[field] != null && !Number.isNaN(Number(r[field]))
  );
  const maxVal = Math.max(1, ...rows.map((r) => Number(r[field] || 0)));
  return rows.map((r) => ({
    id: r.id,
    label: `#${r.id}`,
    ms: Number(r[field] || 0),
    pct: Math.max(3, (Number(r[field] || 0) / maxVal) * 100),
  }));
}

const pipelineCharts = computed(() =>
  PIPELINE_STEPS.map((step) => ({
    ...step,
    bars: barsForField(step.field),
  }))
);

const pipelineCards = computed(() => {
  const s = summary.value;
  if (!s) return [];
  const cards = [
    { label: "Total questions", value: fmtNum(s.total_questions, 0) },
    {
      label: "Avg sentences / turn",
      value: fmtNum(s.avg_stream_chunk_count, 1),
    },
    {
      label: "First / last (UTC)",
      value: `${fmtTs(s.first_event_ts)} → ${fmtTs(s.last_event_ts)}`,
      wide: true,
    },
  ];
  for (const step of PIPELINE_STEPS) {
    cards.push({
      label: `${step.step}. ${step.title} (avg)`,
      value: fmtMs(s[summaryAvgKey(step)]),
      highlight: Boolean(step.highlight),
    });
  }
  return cards;
});

function sentenceRows(row) {
  const chunks = row?.stream_sentence_chunks;
  return Array.isArray(chunks) ? chunks : [];
}

function sentenceCount(row) {
  const rows = sentenceRows(row);
  if (rows.length > 0) return rows.length;
  const n = Number(row?.stream_chunk_count);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function sentenceTotalMs(s) {
  if (s.total_ms != null && !Number.isNaN(Number(s.total_ms))) {
    return Number(s.total_ms);
  }
  const parts = [s.rag_sentence_ms, s.tts_ms, s.humanaudio_ms].filter(
    (v) => v != null && !Number.isNaN(Number(v))
  );
  if (!parts.length) return null;
  return parts.reduce((a, b) => a + Number(b), 0);
}

/** One block per user question with sentence count + every sentence latency. */
const questionTurns = computed(() =>
  recent.value.map((row) => ({
    id: row.id,
    ts: row.ts,
    sttMs: row.stt_latency_ms,
    sentenceCount: sentenceCount(row),
    sentences: sentenceRows(row),
    avgRag: row.stream_avg_rag_sentence_ms,
    avgTts: row.stream_avg_tts_ms,
    avgHa: row.stream_avg_humanaudio_ms,
  }))
);

/** Flat list: every sentence from every question (for scanning all latencies). */
const flatSentenceRows = computed(() => {
  const out = [];
  for (const turn of questionTurns.value) {
    if (!turn.sentences.length) {
      out.push({
        turnId: turn.id,
        ts: turn.ts,
        sentenceCount: turn.sentenceCount,
        sentence: null,
        sttMs: turn.sttMs,
        ragMs: null,
        ttsMs: null,
        haMs: null,
        totalMs: null,
        placeholder: true,
      });
      continue;
    }
    for (const s of turn.sentences) {
      out.push({
        turnId: turn.id,
        ts: turn.ts,
        sentenceCount: turn.sentenceCount,
        sentence: s.sentence,
        sttMs: turn.sttMs,
        ragMs: s.rag_sentence_ms,
        ttsMs: s.tts_ms,
        haMs: s.humanaudio_ms,
        totalMs: sentenceTotalMs(s),
        placeholder: false,
      });
    }
  }
  return out;
});

const totalSentenceRows = computed(() =>
  flatSentenceRows.value.filter((r) => !r.placeholder).length
);

async function submitReset() {
  resetMsg.value = "";
  if (!resetSecret.value.trim()) {
    resetMsg.value = "Enter the configured reset secret.";
    return;
  }
  try {
    const res = await fetch(apiUrl("/api/analytics/reset"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ secret: resetSecret.value.trim() }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      const msg = Array.isArray(d) ? d.map((x) => x.msg || x).join(" ") : d;
      throw new Error(msg || res.statusText || "Reset failed");
    }
    resetMsg.value = `Cleared ${data.cleared ?? 0} record(s).`;
    resetSecret.value = "";
  } catch (e) {
    resetMsg.value = e instanceof Error ? e.message : String(e);
  }
}
</script>

<template>
  <div class="dash">
    <div class="dash__head">
      <div class="dash__head-main">
        <h1 class="dash__title">Analytics</h1>
        <nav class="dash__nav" aria-label="App sections">
          <router-link class="dash-link dash-link--ghost" to="/hologram">Live hologram</router-link>
          <router-link class="dash-link dash-link--ghost" to="/avatar">Avatar Studio</router-link>
          <router-link class="dash-link dash-link--ghost" to="/video-rag">Video RAG</router-link>
        </nav>
      </div>
      <div class="dash__live">
        <span class="dash__dot" :class="`dash__dot--${streamState}`" />
        <span>
          {{ streamState === "live" ? "Live updates" : streamState === "connecting" ? "Connecting stream…" : "Reconnecting…" }}
        </span>
        <span v-if="lastUpdatedAt" class="dash__updated">Last update: {{ fmtTs(lastUpdatedAt) }}</span>
      </div>
    </div>

    <p v-if="err" class="dash__err" role="alert">{{ err }}</p>

    <section v-if="!err && summary" class="pipeline-section">
      <header class="pipeline-section__head">
        <h2 class="pipeline-section__title">Voice latency</h2>
        <p class="pipeline-section__sub">
          Transcribe, then per-sentence RAG → TTS (elapsed_ms) → humanaudiowithpath
        </p>
      </header>

      <div class="viz-grid viz-grid--pipeline">
        <article
          v-for="chart in pipelineCharts"
          :key="chart.id"
          class="viz-card"
          :class="{
            'viz-card--gap': chart.highlight,
          }"
        >
          <div class="viz-card__head">
            <span class="viz-card__step">{{ chart.step }}</span>
            <h3 class="viz-card__title">{{ chart.title }}</h3>
          </div>
          <p v-if="chart.sub" class="viz-card__sub">{{ chart.sub }}</p>
          <p v-if="!chart.bars.length && chart.empty" class="viz-card__empty">
            No samples — {{ chart.empty }}
          </p>
          <p v-else-if="!chart.bars.length" class="viz-card__empty">No samples yet.</p>
          <div v-else class="bars bars--compact">
            <div
              v-for="b in chart.bars"
              :key="`${chart.id}-${b.id}`"
              class="bar-row"
              :title="`${b.label}: ${fmtMs(b.ms)}`"
            >
              <span class="bar-row__label">{{ b.label }}</span>
              <div
                class="bar-row__track"
                :class="chart.track !== 'default' ? `bar-row__track--${chart.track}` : null"
              >
                <div
                  class="bar-row__fill"
                  :class="chart.track !== 'default' ? `bar-row__fill--${chart.track}` : null"
                  :style="{ width: `${b.pct}%` }"
                />
              </div>
              <span class="bar-row__value">{{ fmtMs(b.ms) }}</span>
            </div>
          </div>
        </article>
      </div>

      <div class="dash__grid dash__grid--pipeline">
        <article
          v-for="(c, i) in pipelineCards"
          :key="`p-${i}`"
          class="card card--compact"
          :class="{ 'card--highlight': c.highlight }"
        >
          <h2 class="card__label">{{ c.label }}</h2>
          <p class="card__value">{{ c.value }}</p>
        </article>
      </div>
    </section>

    <section v-if="!err" class="table-section">
      <header class="table-section__head">
        <div>
          <h2 class="table-section__title">Per question · sentence latencies</h2>
          <p class="table-section__sub">
            Each question shows sentence count, transcribe once, then every sentence RAG / TTS / humanaudio ms
          </p>
        </div>
        <p v-if="totalSentenceRows" class="table-section__stat mono">
          {{ totalSentenceRows }} sentence row(s) across {{ questionTurns.length }} question(s)
        </p>
      </header>

      <div v-if="!questionTurns.length && !loading" class="table__empty table__empty--block">
        No data yet — use the mic on the Live page.
      </div>

      <div v-else class="turn-list">
        <article v-for="turn in questionTurns" :key="turn.id" class="turn-card">
          <header class="turn-card__head">
            <div class="turn-card__title-row">
              <h3 class="turn-card__title">Question #{{ turn.id }}</h3>
              <span class="turn-card__badge">{{ turn.sentenceCount }} sentence{{ turn.sentenceCount === 1 ? "" : "s" }}</span>
            </div>
            <p class="turn-card__meta mono">
              <span>{{ fmtTs(turn.ts) }}</span>
              <span class="turn-card__sep">·</span>
              <span>Transcribe {{ fmtMs(turn.sttMs) }}</span>
              <template v-if="turn.avgTts != null">
                <span class="turn-card__sep">·</span>
                <span>avg TTS {{ fmtMs(turn.avgTts) }}</span>
              </template>
              <template v-if="turn.avgHa != null">
                <span class="turn-card__sep">·</span>
                <span>avg humanaudio {{ fmtMs(turn.avgHa) }}</span>
              </template>
            </p>
          </header>

          <div v-if="turn.sentences.length" class="table-wrap table-wrap--inset">
            <table class="sentence-table sentence-table--full">
              <thead>
                <tr>
                  <th>Sentence</th>
                  <th>RAG sentence</th>
                  <th>TTS elapsed_ms</th>
                  <th>humanaudiowithpath</th>
                  <th>Sum</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in turn.sentences" :key="`${turn.id}-s${s.sentence}`">
                  <td class="mono">{{ s.sentence }}</td>
                  <td class="mono">{{ fmtMs(s.rag_sentence_ms) }}</td>
                  <td class="mono">{{ fmtMs(s.tts_ms) }}</td>
                  <td class="mono">{{ fmtMs(s.humanaudio_ms) }}</td>
                  <td class="mono">{{ fmtMs(sentenceTotalMs(s)) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="turn-card__empty">
            No per-sentence data
            <template v-if="turn.sentenceCount > 0"> (count={{ turn.sentenceCount }} from server)</template>
            — run voice-stream with humanaudio mode.
          </p>
        </article>
      </div>

      <h3 class="flat-table__title">All sentence latencies</h3>
      <p class="flat-table__sub">One row per sentence — every question, every sentence</p>
      <div class="table-wrap">
        <table class="table table--flat">
          <thead>
            <tr>
              <th class="table__th--sticky mono">Q#</th>
              <th class="table__th--sticky">Time</th>
              <th class="mono">Sentences</th>
              <th class="mono">Sent #</th>
              <th class="mono">Transcribe</th>
              <th class="mono">RAG sentence</th>
              <th class="mono">TTS elapsed_ms</th>
              <th class="mono">humanaudiowithpath</th>
              <th class="mono">Sum</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(r, idx) in flatSentenceRows"
              :key="r.placeholder ? `ph-${r.turnId}` : `${r.turnId}-s${r.sentence}`"
              :class="{ 'table__row--placeholder': r.placeholder }"
            >
              <td class="table__td--sticky mono">{{ r.turnId }}</td>
              <td class="table__td--sticky">{{ fmtTs(r.ts) }}</td>
              <td class="mono">{{ r.sentenceCount || "—" }}</td>
              <td class="mono">{{ r.sentence ?? "—" }}</td>
              <td class="mono">{{ fmtMs(r.sttMs) }}</td>
              <td class="mono">{{ fmtMs(r.ragMs) }}</td>
              <td class="mono">{{ fmtMs(r.ttsMs) }}</td>
              <td class="mono">{{ fmtMs(r.haMs) }}</td>
              <td class="mono">{{ fmtMs(r.totalMs) }}</td>
            </tr>
            <tr v-if="!flatSentenceRows.length && !loading">
              <td colspan="9" class="table__empty">No sentence rows yet.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="reset-zone">
      <h2 class="reset-zone__title">Reset data</h2>
      <p class="reset-zone__hint">
        Requires <code>ANALYTICS_RESET_SECRET</code> on the server. Leave empty to only refresh stats.
      </p>
      <div class="reset-zone__row">
        <input
          v-model="resetSecret"
          type="password"
          class="reset-zone__input"
          placeholder="Reset secret"
          autocomplete="off"
        />
        <button type="button" class="btn btn--danger" @click="submitReset">Clear all analytics</button>
      </div>
      <p v-if="resetMsg" class="reset-zone__msg">{{ resetMsg }}</p>
    </section>
  </div>
</template>

<style scoped>
.dash {
  flex: 1;
  min-height: 0;
  width: 100%;
  max-width: 100%;
  overflow-x: hidden;
  overflow-y: auto;
  padding: clamp(0.65rem, 2vw, 1.5rem);
  margin: 0 auto;
  box-sizing: border-box;
  background:
    radial-gradient(120rem 40rem at 8% -12%, rgba(56, 189, 248, 0.14), transparent 58%),
    radial-gradient(100rem 40rem at 92% -10%, rgba(139, 92, 246, 0.16), transparent 55%),
    #e4e2e2;
}

.dash__head {
  margin-bottom: 1.25rem;
  padding: 1rem 1.1rem;
  border-radius: 16px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.82), rgba(255, 255, 255, 0.58));
  border: 1px solid rgba(255, 255, 255, 0.72);
  box-shadow:
    0 6px 20px rgba(15, 23, 42, 0.06),
    inset 0 1px 0 rgba(255, 255, 255, 0.75);
  backdrop-filter: blur(8px);
}

.dash__head-main {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.65rem 1rem;
  margin-bottom: 0.6rem;
}

.dash__nav {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.45rem;
}

.dash-link {
  display: inline-flex;
  align-items: center;
  padding: 0.38rem 0.85rem;
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 600;
  text-decoration: none;
  border: 1px solid rgba(148, 163, 184, 0.45);
  transition: background 0.15s ease, border-color 0.15s ease, transform 0.12s ease;
}

.dash-link--ghost {
  background: rgba(255, 255, 255, 0.55);
  color: #334155;
}

.dash-link--ghost:hover {
  background: #fff;
  border-color: rgba(20, 184, 166, 0.45);
  transform: translateY(-1px);
}

.dash__live {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: #444;
  font-size: 0.9rem;
  flex-wrap: wrap;
}

.dash__updated {
  color: #666;
}

.dash__dot {
  width: 0.65rem;
  height: 0.65rem;
  border-radius: 50%;
  background: #888;
}

.dash__dot--live {
  background: #15a34a;
  box-shadow: 0 0 0 4px rgba(21, 163, 74, 0.18);
  animation: pulse-live 1.4s ease-in-out infinite;
}

.dash__dot--connecting,
.dash__dot--reconnecting {
  background: #d97706;
  box-shadow: 0 0 0 4px rgba(217, 119, 6, 0.15);
}

.dash__title {
  margin: 0;
  font-size: clamp(1.55rem, 2.7vw, 2.4rem);
  font-weight: 700;
  color: #111827;
  letter-spacing: 0.01em;
}

.dash__err {
  padding: 0.75rem 1rem;
  border-radius: 10px;
  background: rgba(176, 0, 32, 0.08);
  color: #8b0000;
  margin: 0 0 1rem;
}

.pipeline-section {
  margin-bottom: clamp(1rem, 2vh, 1.5rem);
}

.pipeline-section__head {
  margin-bottom: 0.75rem;
}

.pipeline-section__title {
  margin: 0;
  font-size: clamp(1.05rem, 2vw, 1.25rem);
  font-weight: 700;
  color: #111827;
}

.pipeline-section__sub {
  margin: 0.25rem 0 0;
  font-size: 0.82rem;
  color: #555;
}

.secondary-section {
  margin-bottom: 1rem;
}

.secondary-section__title {
  margin: 0 0 0.65rem;
  font-size: 1rem;
  font-weight: 700;
  color: #374151;
}

.viz-grid {
  display: grid;
  gap: clamp(0.65rem, 1.5vw, 1rem);
  margin-bottom: 0.75rem;
}

.viz-grid--pipeline {
  grid-template-columns: 1fr;
}

@media (min-width: 540px) {
  .viz-grid--pipeline {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (min-width: 900px) {
  .viz-grid--pipeline {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (min-width: 1280px) {
  .viz-grid--pipeline {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

.viz-grid--secondary {
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 16rem), 1fr));
  max-width: 28rem;
}

.viz-card {
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 12px;
  padding: clamp(0.65rem, 1.5vw, 0.9rem);
  box-shadow:
    0 6px 16px rgba(2, 6, 23, 0.06),
    inset 0 1px 0 rgba(255, 255, 255, 0.62);
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.viz-card--compact {
  padding: 0.75rem;
}

.viz-card__head {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  margin-bottom: 0.35rem;
}

.viz-card__step {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.35rem;
  height: 1.35rem;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 800;
  color: #fff;
  background: linear-gradient(135deg, #0891b2, #6366f1);
}

.viz-card__title {
  margin: 0;
  font-size: clamp(0.82rem, 1.6vw, 0.92rem);
  font-weight: 700;
  line-height: 1.25;
}

.viz-card__sub {
  margin: 0 0 0.5rem;
  font-size: 0.72rem;
  color: #555;
  line-height: 1.3;
}

.viz-card__empty {
  margin: 0;
  font-size: 0.85rem;
  color: #666;
}

.viz-card--hero {
  border-color: rgba(8, 145, 178, 0.45);
  background: linear-gradient(180deg, rgba(207, 250, 254, 0.92), rgba(255, 255, 255, 0.96));
}

@media (min-width: 540px) {
  .viz-card--hero {
    grid-column: span 2;
  }
}

@media (min-width: 1280px) {
  .viz-card--hero {
    grid-column: span 2;
  }

  .viz-card--end {
    border-color: rgba(124, 58, 237, 0.35);
  }
}

.viz-card--gap {
  border-color: rgba(217, 119, 6, 0.55);
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.95), rgba(255, 255, 255, 0.96));
}

@media (min-width: 540px) {
  .viz-card--gap {
    grid-column: span 2;
  }
}

.bars {
  display: flex;
  flex-direction: column;
  gap: 0.28rem;
  flex: 1;
  min-height: 0;
  max-height: min(10.5rem, 22vh);
  overflow: auto;
  -webkit-overflow-scrolling: touch;
}

.bars--compact {
  max-height: min(9rem, 20vh);
}

.bar-row {
  display: grid;
  grid-template-columns: 2.25rem minmax(0, 1fr) minmax(3.8rem, 4.6rem);
  align-items: center;
  gap: 0.35rem;
}

.bar-row__label {
  font-size: 0.68rem;
  color: #666;
  font-variant-numeric: tabular-nums;
}

.bar-row__track {
  height: 0.55rem;
  border-radius: 999px;
  background: #e8edf3;
  overflow: hidden;
}

.bar-row__track--tokens {
  background: #ede9fe;
}

.bar-row__track--webrtc {
  background: #d1fae5;
}

.bar-row__fill--webrtc {
  background: linear-gradient(90deg, #10b981, #14b8a6);
}

.bar-row__track--tts {
  background: #fce7f3;
}

.bar-row__fill--tts {
  background: linear-gradient(90deg, #db2777, #ec4899);
}

.bar-row__track--lip {
  background: #ede9fe;
}

.bar-row__fill--lip {
  background: linear-gradient(90deg, #7c3aed, #a855f7);
}

.bar-row__track--lvs {
  background: #ddd6fe;
}

.bar-row__fill--lvs {
  background: linear-gradient(90deg, #5b21b6, #7c3aed, #8b5cf6);
}

.bar-row__track--stt {
  background: #fef3c7;
}

.bar-row__track--speaker {
  background: #cffafe;
}

.bar-row__fill--speaker {
  background: linear-gradient(90deg, #0891b2, #06b6d4, #22d3ee);
}

.bar-row__track--ttfv {
  background: #d1fae5;
}

.bar-row__fill--ttfv {
  background: linear-gradient(90deg, #059669, #10b981, #34d399);
}

.bar-row__fill--stt {
  background: linear-gradient(90deg, #f59e0b, #f97316);
}

.bar-row__track--rag {
  background: #dbeafe;
}

.bar-row__track--stream-tts {
  background: #ffedd5;
}

.bar-row__fill--stream-tts {
  background: linear-gradient(90deg, #ea580c, #f97316);
}

.bar-row__track--humanaudio {
  background: #cffafe;
}

.bar-row__fill--humanaudio {
  background: linear-gradient(90deg, #0891b2, #06b6d4);
}

.bar-row__fill--rag {
  background: linear-gradient(90deg, #3b82f6, #6366f1);
}

.bar-row__fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #06b6d4, #3b82f6);
}

.bar-row__fill--tokens {
  background: linear-gradient(90deg, #8b5cf6, #ec4899);
}

.bar-row__value {
  font-size: 0.68rem;
  color: #333;
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.dash__grid {
  display: grid;
  gap: clamp(0.5rem, 1.2vw, 0.75rem);
  margin-bottom: 0.5rem;
}

.dash__grid--pipeline {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 9.5rem), 1fr));
}

.dash__grid--secondary {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 11rem), 1fr));
  margin-bottom: 1.25rem;
}

.card {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(255, 255, 255, 0.94));
  border: 1px solid rgba(0, 0, 0, 0.07);
  border-radius: 12px;
  padding: clamp(0.55rem, 1.2vw, 0.85rem) clamp(0.65rem, 1.5vw, 1rem);
  box-shadow:
    0 6px 16px rgba(2, 6, 23, 0.06),
    inset 0 1px 0 rgba(255, 255, 255, 0.62);
  min-width: 0;
}

.card--compact .card__label {
  font-size: 0.68rem;
  letter-spacing: 0.04em;
}

.card--compact .card__value {
  font-size: clamp(0.95rem, 1.8vw, 1.2rem);
}

.card--wide {
  grid-column: 1 / -1;
}

.card--highlight {
  border-color: rgba(8, 145, 178, 0.45);
  background: linear-gradient(180deg, rgba(207, 250, 254, 0.98), rgba(255, 255, 255, 0.96));
}

.card--highlight .card__value {
  color: #0e7490;
}

.card__label {
  margin: 0 0 0.4rem;
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #666;
}

.card__value {
  margin: 0;
  font-size: clamp(1.25rem, 2vw, 1.65rem);
  font-weight: 700;
  color: #111;
  word-break: break-word;
}

.card__value--small {
  font-size: clamp(0.95rem, 1.4vw, 1.15rem);
  font-weight: 600;
}

.table-section {
  margin-bottom: 1rem;
}

.table-section__title {
  margin: 0 0 0.25rem;
  font-size: clamp(1rem, 2vw, 1.15rem);
  font-weight: 700;
}

.table-section__sub {
  margin: 0 0 0.6rem;
  font-size: 0.78rem;
  color: #555;
}

.table-wrap {
  overflow: auto;
  max-width: 100%;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 6px 16px rgba(2, 6, 23, 0.06);
  -webkit-overflow-scrolling: touch;
}

.table {
  width: 100%;
  min-width: 44rem;
  border-collapse: collapse;
  font-size: clamp(0.72rem, 1.1vw, 0.88rem);
}

.table th,
.table td {
  padding: 0.45rem 0.55rem;
  text-align: left;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.table th {
  background: #f6f6f6;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
  position: sticky;
  top: 0;
  z-index: 2;
}

.table__th--sticky,
.table__td--sticky {
  position: sticky;
  left: 0;
  z-index: 1;
  background: inherit;
}

.table__th--sticky {
  z-index: 3;
  background: #f0f4f8;
}

.table__td--sticky {
  background: #fff;
}

.table tbody tr:hover .table__td--sticky {
  background: #f0fdfa;
}

.table__th--pipe,
.table__td--pipe {
  background: rgba(236, 253, 245, 0.35);
}

.table__th--pipe {
  background: #ecfdf5;
}

.table__td--hero {
  font-weight: 700;
  color: #0e7490;
}

.table tbody tr:hover .table__td--hero {
  color: #047857;
}

.table tbody tr:hover {
  background: rgba(0, 180, 180, 0.06);
}

.table__empty {
  text-align: center;
  color: #666;
  padding: 2rem !important;
}

.table-section__head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 1rem;
}

.table-section__stat {
  margin: 0;
  font-size: 0.88rem;
  color: #444;
  padding: 0.35rem 0.75rem;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid rgba(0, 0, 0, 0.06);
}

.table__empty--block {
  margin-bottom: 1.5rem;
}

.turn-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  margin-bottom: 2rem;
}

.turn-card {
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid rgba(255, 255, 255, 0.85);
  box-shadow: 0 4px 16px rgba(15, 23, 42, 0.05);
  overflow: hidden;
}

.turn-card__head {
  padding: 0.85rem 1rem;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  background: linear-gradient(135deg, rgba(0, 180, 180, 0.08), rgba(107, 82, 216, 0.06));
}

.turn-card__title-row {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  flex-wrap: wrap;
}

.turn-card__title {
  margin: 0;
  font-size: 1rem;
  font-weight: 700;
}

.turn-card__badge {
  display: inline-flex;
  align-items: center;
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 700;
  color: #0e7490;
  background: rgba(0, 180, 180, 0.14);
  border: 1px solid rgba(0, 180, 180, 0.25);
}

.turn-card__meta {
  margin: 0.35rem 0 0;
  font-size: 0.82rem;
  color: #555;
}

.turn-card__sep {
  margin: 0 0.35rem;
  opacity: 0.55;
}

.turn-card__empty {
  margin: 0;
  padding: 0.85rem 1rem;
  font-size: 0.85rem;
  color: #666;
}

.table-wrap--inset {
  margin: 0;
}

.sentence-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
}

.sentence-table--full th,
.sentence-table--full td {
  padding: 0.45rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.sentence-table th {
  color: #555;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.5);
}

.flat-table__title {
  margin: 0 0 0.25rem;
  font-size: 1.05rem;
  font-weight: 700;
}

.flat-table__sub {
  margin: 0 0 0.75rem;
  font-size: 0.85rem;
  color: #555;
}

.table--flat thead th {
  font-size: 0.78rem;
  white-space: nowrap;
}

.table__row--placeholder {
  opacity: 0.65;
  font-style: italic;
}

.mono {
  font-variant-numeric: tabular-nums;
}

.btn {
  padding: 0.5rem 1.1rem;
  border-radius: 999px;
  border: none;
  font: inherit;
  font-weight: 600;
  font-size: 0.9rem;
  cursor: pointer;
  color: #fff;
  background: linear-gradient(135deg, #00b4b4, #6b52d8);
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn--danger {
  background: linear-gradient(135deg, #c62828, #8e24aa);
}

.reset-zone {
  margin-top: clamp(1rem, 3vh, 2rem);
  padding-top: 1rem;
  border-top: 1px solid rgba(0, 0, 0, 0.1);
}

@media (max-width: 539px) {
  .dash__head {
    padding: 0.75rem 0.85rem;
  }

  .dash__live {
    font-size: 0.8rem;
  }

  .viz-card--hero {
    grid-column: 1 / -1;
  }
}

.reset-zone__title {
  margin: 0 0 0.35rem;
  font-size: 1.05rem;
  font-weight: 700;
}

.reset-zone__hint {
  margin: 0 0 0.75rem;
  font-size: 0.85rem;
  color: #555;
}

.reset-zone__row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
}

.reset-zone__input {
  min-width: 12rem;
  flex: 1;
  max-width: 24rem;
  padding: 0.5rem 0.75rem;
  border-radius: 10px;
  border: 1px solid #ccc;
  font: inherit;
}

.reset-zone__msg {
  margin: 0.5rem 0 0;
  font-size: 0.9rem;
  color: #333;
}

@keyframes pulse-live {
  50% {
    box-shadow: 0 0 0 6px rgba(21, 163, 74, 0.1);
  }
}
</style>
