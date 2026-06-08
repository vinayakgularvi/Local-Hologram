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

const recentTableRows = computed(() => recent.value);

/** Unique pipeline metrics only (mic tap → lip-sync avatar). No duplicate aliases. */
const PIPELINE_STEPS = [
  {
    id: "mic",
    step: 1,
    title: "Mic tap → lip-sync avatar",
    sub: "End-to-end until avatar is visibly playing",
    field: "mic_to_lip_sync_ms",
    track: "speaker",
    hero: true,
    empty: "Complete a spoken turn on Live with avatar video.",
  },
  {
    id: "mica",
    step: 2,
    title: "Mic tap → first audio",
    sub: "Until avatar speaks on speakers",
    field: "mic_to_first_audio_ms",
    track: "ttfv",
    empty: "Complete a spoken turn with TTS.",
  },
  {
    id: "stt",
    step: 3,
    title: "Transcribe API (final)",
    sub: "Final POST /api/transcribe request → response (ms)",
    field: "stt_latency_ms",
    track: "stt",
    empty: "Complete a spoken turn with server transcribe enabled.",
  },
  {
    id: "stt-live",
    step: 4,
    title: "Live transcribe first chunk",
    sub: "Mic tap → first partial caption (chunked STT while recording)",
    field: "stt_first_chunk_latency_ms",
    track: "stt",
    empty: "Speak for a few seconds with live chunked transcribe enabled.",
  },
  {
    id: "stt-lip",
    step: 5,
    title: "Transcribe → lip-sync video",
    sub: "Final transcribe response → avatar visibly lip-syncing",
    field: "stt_to_lip_sync_ms",
    track: "stt",
    highlight: true,
    empty: "Complete a spoken turn with server transcribe and avatar video.",
  },
  {
    id: "cvt",
    step: 6,
    title: "Client voice-turn",
    sub: "Transcript sent → voice-turn JSON",
    field: "client_voice_turn_ms",
    track: "rag",
    empty: "Complete a spoken voice-turn on Live.",
  },
  {
    id: "rag",
    step: 7,
    title: "RAG latency",
    sub: "Stream request → response (when configured)",
    field: "rag_latency_ms",
    track: "rag",
    empty: "Voice turns using RAG_GENERATE_STREAM_URL only.",
  },
  {
    id: "rag-chunk",
    step: 8,
    title: "RAG → first sentence",
    sub: "Stream start → first complete sentence enqueued for TTS",
    field: "rag_first_chunk_enqueued_ms",
    track: "rag",
    empty: "Voice-stream turns with sentence dispatch (VOICE_STREAM_HUMAN_UNIT=sentence).",
  },
  {
    id: "tts-sentence-1",
    step: 9,
    title: "Sentence TTS (1st)",
    sub: "POST /v1/tts/reference latency for the first streamed sentence",
    field: "stream_first_tts_ms",
    track: "stream-tts",
    highlight: true,
    empty: "Voice-stream with VOICE_DISPATCH_MODE=humanaudio.",
  },
  {
    id: "ha-1",
    step: 10,
    title: "humanaudio (1st sentence)",
    sub: "POST /humanaudiowithpath for first sentence",
    field: "stream_first_humanaudio_ms",
    track: "humanaudio",
    highlight: true,
    empty: "Voice-stream with VOICE_DISPATCH_MODE=humanaudio.",
  },
  {
    id: "sentence-total-1",
    step: 11,
    title: "1st sentence TTS + humanaudio",
    sub: "Sum of /v1/tts/reference + /humanaudiowithpath for first sentence",
    field: "stream_first_chunk_total_ms",
    track: "stream-tts",
    highlight: true,
    empty: "Voice-stream with VOICE_DISPATCH_MODE=humanaudio.",
  },
  {
    id: "rag-first-audio",
    step: 12,
    title: "RAG → first sentence audio",
    sub: "RAG stream start → first sentence sent to avatar (wall clock)",
    field: "stream_first_chunk_completed_ms",
    track: "rag",
    highlight: true,
    empty: "Voice-stream with sentence dispatch and humanaudio.",
  },
  {
    id: "tts-sentence-avg",
    step: 13,
    title: "Sentence TTS (avg)",
    sub: "Average /v1/tts/reference latency per streamed sentence",
    field: "stream_avg_tts_ms",
    track: "stream-tts",
    empty: "Voice-stream with multiple sentences in one reply.",
  },
  {
    id: "ha-avg",
    step: 14,
    title: "humanaudio (avg / sentence)",
    sub: "Average /humanaudiowithpath POST latency per sentence",
    field: "stream_avg_humanaudio_ms",
    track: "humanaudio",
    empty: "Voice-stream with multiple sentences in one reply.",
  },
  {
    id: "server",
    step: 15,
    title: "Server voice-stream",
    sub: "API wall time (until JSON returned)",
    field: "total_request_ms",
    track: "default",
    empty: null,
  },
  {
    id: "human",
    step: 16,
    title: "Human dispatch",
    sub: "Server POST LiveTalking /human (legacy)",
    field: "human_dispatch_ms",
    track: "webrtc",
    empty: "Requires VOICE_DISPATCH_MODE=human.",
  },
  {
    id: "tts",
    step: 17,
    title: "Server complete → WebRTC audio",
    sub: "Voice-stream JSON received → audible audio on stream",
    field: "tts_latency_ms",
    track: "tts",
    empty: "Complete a spoken turn with TTS.",
  },
  {
    id: "realpb",
    step: 18,
    title: "Server complete → real WebRTC playback",
    sub: "Audible audio + lip-sync video (timestamp = later of the two)",
    field: "webrtc_real_playback_ms",
    track: "speaker",
    highlight: true,
    empty: "Complete a turn with avatar video and TTS.",
  },
  {
    id: "avatar",
    step: 19,
    title: "Server complete → lip-sync video",
    sub: "Voice-turn JSON received → avatar visibly playing",
    field: "lip_sync_avatar_play_ms",
    track: "speaker",
    highlight: true,
    empty: "Complete a turn with visible avatar video.",
  },
  {
    id: "vfirst",
    step: 20,
    title: "Video stream first tick",
    sub: "Early timeline bump (may be stale frames)",
    field: "video_stream_first_ms",
    track: "lvs",
    empty: "Complete a turn with visible avatar video.",
  },
  {
    id: "gap",
    step: 21,
    title: "Stream tick → avatar play",
    sub: "Gap after first tick until lip-synced avatar plays",
    field: "stream_start_to_avatar_ms",
    track: "lip",
    empty: "Complete a turn with visible avatar video.",
  },
  {
    id: "lip",
    step: 22,
    title: "Audio → lip-sync video",
    sub: "First audio → lip-synced avatar playing",
    field: "lip_sync_latency_ms",
    track: "lip",
    empty: "Complete a turn with visible avatar video.",
  },
  {
    id: "ttfv",
    step: 23,
    title: "Time to first voice",
    sub: "STT + RAG (or server) + WebRTC audio (sum)",
    field: "time_to_first_voice_ms",
    track: "ttfv",
    empty: "Complete a spoken turn with TTS.",
  },
];

function summaryAvgKey(field) {
  if (field === "total_request_ms") return "avg_total_ms";
  return `avg_${field}`;
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
      label: "Server /human dispatched",
      value: `${fmtNum(s.human_dispatched_count, 0)} / ${fmtNum(s.total_questions, 0)}`,
    },
  ];
  for (const step of PIPELINE_STEPS) {
    cards.push({
      label: `${step.step}. ${step.title} (avg)`,
      value: fmtMs(s[summaryAvgKey(step.field)]),
      highlight: Boolean(step.hero || step.highlight),
    });
  }
  return cards;
});

const tableColumns = computed(() => [
  { key: "id", label: "#", sticky: true },
  { key: "ts", label: "Time (UTC)", sticky: true },
  ...PIPELINE_STEPS.map((step) => ({
    key: step.field,
    label: String(step.step),
    mono: true,
    pipeline: true,
    hero: Boolean(step.hero),
  })),
  { key: "human_dispatched", label: "Disp.", mono: true },
  { key: "prompt_tokens", label: "Tokens", mono: true },
  { key: "heard_chars", label: "Chars", mono: true },
]);

const secondaryCards = computed(() => {
  const s = summary.value;
  if (!s) return [];
  return [
    { label: "Prompt tokens (sum)", value: fmtNum(s.sum_prompt_tokens, 0) },
    { label: "Completion tokens (sum)", value: fmtNum(s.sum_completion_tokens, 0) },
    { label: "Total tokens (sum)", value: fmtNum(s.sum_total_tokens, 0) },
    {
      label: "Avg heard / answer chars",
      value: `${fmtNum(s.avg_heard_chars, 1)} / ${fmtNum(s.avg_answer_chars, 1)}`,
    },
    {
      label: "First / last event (UTC)",
      value: `${fmtTs(s.first_event_ts)} → ${fmtTs(s.last_event_ts)}`,
      wide: true,
    },
  ];
});

function tableCell(row, col) {
  if (col.key === "id") return row.id;
  if (col.key === "ts") return fmtTs(row.ts);
  if (col.key === "human_dispatched") {
    return row.human_dispatched === 1 ? "yes" : row.human_dispatched === 0 ? "no" : "—";
  }
  if (col.key === "prompt_tokens") {
    return `${row.prompt_tokens ?? "—"} / ${row.completion_tokens ?? "—"}`;
  }
  if (col.key === "heard_chars") {
    return `${row.heard_chars} / ${row.answer_chars}`;
  }
  return fmtMs(row[col.key]);
}

const recentTokenBars = computed(() => {
  const rows = latestChartRows(recent.value);
  const maxVal = Math.max(
    1,
    ...rows.map((r) => Number((r.prompt_tokens || 0) + (r.completion_tokens || 0)))
  );
  return rows.map((r) => {
    const total = Number((r.prompt_tokens || 0) + (r.completion_tokens || 0));
    return {
      id: r.id,
      label: `#${r.id}`,
      total,
      pct: Math.max(3, (total / maxVal) * 100),
    };
  });
});

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
        <h2 class="pipeline-section__title">Voice pipeline</h2>
        <p class="pipeline-section__sub">
          Unique metrics, mic tap → lip-sync avatar; steps ⑧–⑫ are per-sentence TTS / humanaudio
        </p>
      </header>

      <div class="viz-grid viz-grid--pipeline">
        <article
          v-for="chart in pipelineCharts"
          :key="chart.id"
          class="viz-card"
          :class="{
            'viz-card--hero': chart.hero,
            'viz-card--gap': chart.highlight,
            'viz-card--end': chart.id === 'ttfv',
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

    <section v-if="!err && summary" class="secondary-section">
      <h2 class="secondary-section__title">Tokens &amp; usage</h2>
      <div class="viz-grid viz-grid--secondary">
        <article class="viz-card viz-card--compact">
          <h3 class="viz-card__title">Recent token usage</h3>
          <div v-if="recentTokenBars.length" class="bars bars--compact">
            <div
              v-for="b in recentTokenBars"
              :key="`t-${b.id}`"
              class="bar-row"
              :title="`${b.label}: ${fmtNum(b.total)}`"
            >
              <span class="bar-row__label">{{ b.label }}</span>
              <div class="bar-row__track bar-row__track--tokens">
                <div class="bar-row__fill bar-row__fill--tokens" :style="{ width: `${b.pct}%` }" />
              </div>
              <span class="bar-row__value">{{ fmtNum(b.total) }}</span>
            </div>
          </div>
          <p v-else class="viz-card__empty">No token data yet.</p>
        </article>
      </div>
      <div class="dash__grid dash__grid--secondary">
        <article
          v-for="(c, i) in secondaryCards"
          :key="`s-${i}`"
          class="card card--compact"
          :class="{ 'card--wide': c.wide }"
        >
          <h2 class="card__label">{{ c.label }}</h2>
          <p class="card__value" :class="{ 'card__value--small': c.wide }">{{ c.value }}</p>
        </article>
      </div>
    </section>

    <section v-if="!err" class="table-section">
      <h2 class="table-section__title">Recent voice turns</h2>
      <p class="table-section__sub">Columns follow pipeline order (mic tap → lip sync video)</p>
      <div class="table-wrap">
        <table class="table table--pipeline">
          <thead>
            <tr>
              <th
                v-for="col in tableColumns"
                :key="col.key"
                :class="{
                  mono: col.mono,
                  'table__th--sticky': col.sticky,
                  'table__th--pipe': col.pipeline,
                }"
              >
                {{ col.label }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in recentTableRows" :key="row.id">
              <td
                v-for="col in tableColumns"
                :key="`${row.id}-${col.key}`"
                :class="{
                  mono: col.mono,
                  'table__td--sticky': col.sticky,
                  'table__td--pipe': col.pipeline,
                  'table__td--hero': col.hero,
                }"
              >
                {{ tableCell(row, col) }}
              </td>
            </tr>
            <tr v-if="!recentTableRows.length && !loading">
              <td :colspan="tableColumns.length" class="table__empty">
                No data yet — use the mic on the Live page.
              </td>
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
