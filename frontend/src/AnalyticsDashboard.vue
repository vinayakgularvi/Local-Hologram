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

const cards = computed(() => {
  const s = summary.value;
  if (!s) return [];
  return [
    { label: "Total questions (voice)", value: fmtNum(s.total_questions, 0) },
    {
      label: "Avg mic tap → speaker",
      value: fmtMs(s.avg_mic_to_speaker_voice_ms),
      highlight: true,
    },
    {
      label: "Min / max mic → speaker",
      value: `${fmtMs(s.min_mic_to_speaker_voice_ms)} / ${fmtMs(s.max_mic_to_speaker_voice_ms)}`,
      highlight: true,
    },
    {
      label: "Avg time to first voice",
      value: fmtMs(s.avg_time_to_first_voice_ms),
    },
    {
      label: "Min / max time to first voice",
      value: `${fmtMs(s.min_time_to_first_voice_ms)} / ${fmtMs(s.max_time_to_first_voice_ms)}`,
    },
    { label: "Avg speech-to-text", value: fmtMs(s.avg_stt_latency_ms) },
    { label: "Avg RAG latency", value: fmtMs(s.avg_rag_latency_ms) },
    {
      label: "Avg WebRTC voice",
      value: fmtMs(s.avg_webrtc_first_voice_ms),
    },
    { label: "Avg server voice-turn", value: fmtMs(s.avg_total_ms) },
    { label: "Min / max latency", value: `${fmtMs(s.min_total_ms)} / ${fmtMs(s.max_total_ms)}` },
    {
      label: "Min / max STT",
      value: `${fmtMs(s.min_stt_latency_ms)} / ${fmtMs(s.max_stt_latency_ms)}`,
    },
    {
      label: "Min / max RAG latency",
      value: `${fmtMs(s.min_rag_latency_ms)} / ${fmtMs(s.max_rag_latency_ms)}`,
    },
    {
      label: "Min / max WebRTC voice",
      value: `${fmtMs(s.min_webrtc_first_voice_ms)} / ${fmtMs(s.max_webrtc_first_voice_ms)}`,
    },
    { label: "Prompt tokens (sum)", value: fmtNum(s.sum_prompt_tokens, 0) },
    { label: "Completion tokens (sum)", value: fmtNum(s.sum_completion_tokens, 0) },
    { label: "Total tokens (sum)", value: fmtNum(s.sum_total_tokens, 0) },
    { label: "Avg heard / answer chars", value: `${fmtNum(s.avg_heard_chars, 1)} / ${fmtNum(s.avg_answer_chars, 1)}` },
  ];
});

const recentMicToSpeakerBars = computed(() => {
  const rows = latestChartRows(
    recent.value,
    24,
    (r) =>
      r.mic_to_speaker_voice_ms != null && !Number.isNaN(Number(r.mic_to_speaker_voice_ms))
  );
  const maxVal = Math.max(1, ...rows.map((r) => Number(r.mic_to_speaker_voice_ms || 0)));
  return rows.map((r) => ({
    id: r.id,
    label: `#${r.id}`,
    ms: Number(r.mic_to_speaker_voice_ms || 0),
    pct: Math.max(3, (Number(r.mic_to_speaker_voice_ms || 0) / maxVal) * 100),
  }));
});

const recentTimeToFirstVoiceBars = computed(() => {
  const rows = latestChartRows(
    recent.value,
    24,
    (r) =>
      r.time_to_first_voice_ms != null && !Number.isNaN(Number(r.time_to_first_voice_ms))
  );
  const maxVal = Math.max(1, ...rows.map((r) => Number(r.time_to_first_voice_ms || 0)));
  return rows.map((r) => ({
    id: r.id,
    label: `#${r.id}`,
    ms: Number(r.time_to_first_voice_ms || 0),
    pct: Math.max(3, (Number(r.time_to_first_voice_ms || 0) / maxVal) * 100),
  }));
});

const recentLatencyBars = computed(() => {
  const rows = latestChartRows(recent.value);
  const maxVal = Math.max(1, ...rows.map((r) => Number(r.total_request_ms || 0)));
  return rows.map((r) => ({
    id: r.id,
    label: `#${r.id}`,
    ms: Number(r.total_request_ms || 0),
    pct: Math.max(3, (Number(r.total_request_ms || 0) / maxVal) * 100),
  }));
});

const recentSttLatencyBars = computed(() => {
  const rows = latestChartRows(
    recent.value,
    24,
    (r) => r.stt_latency_ms != null && !Number.isNaN(Number(r.stt_latency_ms))
  );
  const maxVal = Math.max(1, ...rows.map((r) => Number(r.stt_latency_ms || 0)));
  return rows.map((r) => ({
    id: r.id,
    label: `#${r.id}`,
    ms: Number(r.stt_latency_ms || 0),
    pct: Math.max(3, (Number(r.stt_latency_ms || 0) / maxVal) * 100),
  }));
});

const recentRagLatencyBars = computed(() => {
  const rows = latestChartRows(
    recent.value,
    24,
    (r) => r.rag_latency_ms != null && !Number.isNaN(Number(r.rag_latency_ms))
  );
  const maxVal = Math.max(1, ...rows.map((r) => Number(r.rag_latency_ms || 0)));
  return rows.map((r) => ({
    id: r.id,
    label: `#${r.id}`,
    ms: Number(r.rag_latency_ms || 0),
    pct: Math.max(3, (Number(r.rag_latency_ms || 0) / maxVal) * 100),
  }));
});

const recentWebrtcVoiceBars = computed(() => {
  const rows = latestChartRows(
    recent.value,
    24,
    (r) =>
      r.webrtc_first_voice_ms != null && !Number.isNaN(Number(r.webrtc_first_voice_ms))
  );
  const maxVal = Math.max(1, ...rows.map((r) => Number(r.webrtc_first_voice_ms || 0)));
  return rows.map((r) => ({
    id: r.id,
    label: `#${r.id}`,
    ms: Number(r.webrtc_first_voice_ms || 0),
    pct: Math.max(3, (Number(r.webrtc_first_voice_ms || 0) / maxVal) * 100),
  }));
});

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

    <section v-if="!err && summary" class="viz-grid">
      <article class="viz-card viz-card--ttfv">
        <h2 class="viz-card__title">Mic tap → speaker audio</h2>
        <p class="viz-card__sub">
          From mic tap until avatar audio plays on speakers (full perceived wait)
        </p>
        <p v-if="!recentMicToSpeakerBars.length" class="viz-card__empty">
          No samples yet — complete a spoken turn on Live with TTS.
        </p>
        <div v-else class="bars">
          <div
            v-for="b in recentMicToSpeakerBars"
            :key="`m-${b.id}`"
            class="bar-row"
            :title="`${b.label}: ${fmtMs(b.ms)}`"
          >
            <span class="bar-row__label">{{ b.label }}</span>
            <div class="bar-row__track bar-row__track--speaker">
              <div class="bar-row__fill bar-row__fill--speaker" :style="{ width: `${b.pct}%` }" />
            </div>
            <span class="bar-row__value">{{ fmtMs(b.ms) }}</span>
          </div>
        </div>
      </article>
      <article class="viz-card">
        <h2 class="viz-card__title">Time to first voice (sum)</h2>
        <p class="viz-card__sub">
          STT + RAG stream (or server voice-turn) + WebRTC (request → audio)
        </p>
        <p v-if="!recentTimeToFirstVoiceBars.length" class="viz-card__empty">
          No samples yet — complete a spoken turn on Live with TTS.
        </p>
        <div v-else class="bars">
          <div
            v-for="b in recentTimeToFirstVoiceBars"
            :key="`v-${b.id}`"
            class="bar-row"
            :title="`${b.label}: ${fmtMs(b.ms)}`"
          >
            <span class="bar-row__label">{{ b.label }}</span>
            <div class="bar-row__track bar-row__track--ttfv">
              <div class="bar-row__fill bar-row__fill--ttfv" :style="{ width: `${b.pct}%` }" />
            </div>
            <span class="bar-row__value">{{ fmtMs(b.ms) }}</span>
          </div>
        </div>
      </article>
      <article class="viz-card">
        <h2 class="viz-card__title">Server voice-turn latency</h2>
        <div class="bars">
          <div v-for="b in recentLatencyBars" :key="`l-${b.id}`" class="bar-row" :title="`${b.label}: ${fmtMs(b.ms)}`">
            <span class="bar-row__label">{{ b.label }}</span>
            <div class="bar-row__track"><div class="bar-row__fill" :style="{ width: `${b.pct}%` }" /></div>
            <span class="bar-row__value">{{ fmtMs(b.ms) }}</span>
          </div>
        </div>
      </article>
      <article class="viz-card">
        <h2 class="viz-card__title">Speech-to-text (mic → transcript)</h2>
        <p v-if="!recentSttLatencyBars.length" class="viz-card__empty">
          No samples yet — use the mic on the Live page.
        </p>
        <div v-else class="bars">
          <div
            v-for="b in recentSttLatencyBars"
            :key="`s-${b.id}`"
            class="bar-row"
            :title="`${b.label}: ${fmtMs(b.ms)}`"
          >
            <span class="bar-row__label">{{ b.label }}</span>
            <div class="bar-row__track bar-row__track--stt">
              <div class="bar-row__fill bar-row__fill--stt" :style="{ width: `${b.pct}%` }" />
            </div>
            <span class="bar-row__value">{{ fmtMs(b.ms) }}</span>
          </div>
        </div>
      </article>
      <article class="viz-card">
        <h2 class="viz-card__title">RAG latency (stream request → response)</h2>
        <p v-if="!recentRagLatencyBars.length" class="viz-card__empty">
          No samples yet — voice turns using RAG_GENERATE_STREAM_URL only.
        </p>
        <div v-else class="bars">
          <div
            v-for="b in recentRagLatencyBars"
            :key="`r-${b.id}`"
            class="bar-row"
            :title="`${b.label}: ${fmtMs(b.ms)}`"
          >
            <span class="bar-row__label">{{ b.label }}</span>
            <div class="bar-row__track bar-row__track--rag">
              <div class="bar-row__fill bar-row__fill--rag" :style="{ width: `${b.pct}%` }" />
            </div>
            <span class="bar-row__value">{{ fmtMs(b.ms) }}</span>
          </div>
        </div>
      </article>
      <article class="viz-card">
        <h2 class="viz-card__title">First WebRTC voice (request → audio)</h2>
        <p v-if="!recentWebrtcVoiceBars.length" class="viz-card__empty">
          No samples yet — speak on Live after a voice-turn with TTS.
        </p>
        <div v-else class="bars">
          <div
            v-for="b in recentWebrtcVoiceBars"
            :key="`w-${b.id}`"
            class="bar-row"
            :title="`${b.label}: ${fmtMs(b.ms)}`"
          >
            <span class="bar-row__label">{{ b.label }}</span>
            <div class="bar-row__track bar-row__track--webrtc">
              <div class="bar-row__fill bar-row__fill--webrtc" :style="{ width: `${b.pct}%` }" />
            </div>
            <span class="bar-row__value">{{ fmtMs(b.ms) }}</span>
          </div>
        </div>
      </article>
      <article class="viz-card">
        <h2 class="viz-card__title">Recent token usage</h2>
        <div class="bars">
          <div v-for="b in recentTokenBars" :key="`t-${b.id}`" class="bar-row" :title="`${b.label}: ${fmtNum(b.total)}`">
            <span class="bar-row__label">{{ b.label }}</span>
            <div class="bar-row__track bar-row__track--tokens"><div class="bar-row__fill bar-row__fill--tokens" :style="{ width: `${b.pct}%` }" /></div>
            <span class="bar-row__value">{{ fmtNum(b.total) }}</span>
          </div>
        </div>
      </article>
    </section>

    <div v-if="!err && summary" class="dash__grid">
      <article
        v-for="(c, i) in cards"
        :key="i"
        class="card"
        :class="{ 'card--highlight': c.highlight }"
      >
        <h2 class="card__label">{{ c.label }}</h2>
        <p class="card__value">{{ c.value }}</p>
      </article>
      <article class="card card--wide">
        <h2 class="card__label">First / last event (UTC)</h2>
        <p class="card__value card__value--small">
          {{ fmtTs(summary.first_event_ts) }} → {{ fmtTs(summary.last_event_ts) }}
        </p>
      </article>
    </div>

    <section v-if="!err" class="table-section">
      <h2 class="table-section__title">Recent voice turns</h2>
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Time (UTC)</th>
              <th>Mic → speaker</th>
              <th>Time to first voice</th>
              <th>Speech-to-text</th>
              <th>RAG latency</th>
              <th>WebRTC voice</th>
              <th>Server turn</th>
              <th>Tokens (p / c)</th>
              <th>Heard / answer chars</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in recentTableRows" :key="row.id">
              <td>{{ row.id }}</td>
              <td class="mono">{{ fmtTs(row.ts) }}</td>
              <td class="mono">{{ fmtMs(row.mic_to_speaker_voice_ms) }}</td>
              <td class="mono">{{ fmtMs(row.time_to_first_voice_ms) }}</td>
              <td class="mono">{{ fmtMs(row.stt_latency_ms) }}</td>
              <td class="mono">{{ fmtMs(row.rag_latency_ms) }}</td>
              <td class="mono">{{ fmtMs(row.webrtc_first_voice_ms) }}</td>
              <td class="mono">{{ fmtMs(row.total_request_ms) }}</td>
              <td class="mono">
                {{ row.prompt_tokens ?? "—" }} / {{ row.completion_tokens ?? "—" }}
              </td>
              <td class="mono">{{ row.heard_chars }} / {{ row.answer_chars }}</td>
            </tr>
            <tr v-if="!recentTableRows.length && !loading">
              <td colspan="10" class="table__empty">No data yet — use the mic on the Live page.</td>
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
  overflow: auto;
  padding: clamp(1rem, 2.5vw, 2.5rem);
  max-width: 120rem;
  margin: 0 auto;
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

.viz-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(22rem, 1fr));
  gap: 1rem;
  margin-bottom: 1rem;
}

.viz-card {
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 14px;
  padding: 1rem;
  box-shadow:
    0 8px 22px rgba(2, 6, 23, 0.08),
    inset 0 1px 0 rgba(255, 255, 255, 0.62);
}

.viz-card__title {
  margin: 0 0 0.75rem;
  font-size: 0.95rem;
  font-weight: 700;
}

.viz-card__sub {
  margin: -0.35rem 0 0.75rem;
  font-size: 0.8rem;
  color: #555;
  line-height: 1.35;
}

.viz-card__empty {
  margin: 0;
  font-size: 0.85rem;
  color: #666;
}

.viz-card--ttfv {
  grid-column: 1 / -1;
  border-color: rgba(16, 185, 129, 0.35);
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.95), rgba(255, 255, 255, 0.94));
}

.bars {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  max-height: 21rem;
  overflow: auto;
}

.bar-row {
  display: grid;
  grid-template-columns: 2.6rem 1fr 5.2rem;
  align-items: center;
  gap: 0.5rem;
}

.bar-row__label {
  font-size: 0.75rem;
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
  font-size: 0.76rem;
  color: #333;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.dash__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
  gap: 1rem;
  margin-bottom: 2rem;
}

.card {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(255, 255, 255, 0.94));
  border: 1px solid rgba(0, 0, 0, 0.07);
  border-radius: 14px;
  padding: 1.1rem 1.25rem;
  box-shadow:
    0 8px 22px rgba(2, 6, 23, 0.08),
    inset 0 1px 0 rgba(255, 255, 255, 0.62);
}

.card--wide {
  grid-column: 1 / -1;
}

.card--highlight {
  border-color: rgba(16, 185, 129, 0.4);
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.98), rgba(255, 255, 255, 0.96));
}

.card--highlight .card__value {
  color: #047857;
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

.table-section__title {
  margin: 0 0 0.75rem;
  font-size: 1.15rem;
  font-weight: 700;
}

.table-wrap {
  overflow: auto;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 8px 20px rgba(2, 6, 23, 0.07);
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: clamp(0.8rem, 1.2vw, 0.95rem);
}

.table th,
.table td {
  padding: 0.65rem 0.85rem;
  text-align: left;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.table th {
  background: #f6f6f6;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
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
  margin-top: 2.5rem;
  padding-top: 1.5rem;
  border-top: 1px solid rgba(0, 0, 0, 0.1);
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
