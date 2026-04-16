<script setup>
import { computed, onMounted, ref } from "vue";

const loading = ref(true);
const err = ref("");
const summary = ref(null);
const recent = ref([]);
const resetSecret = ref("");
const resetMsg = ref("");

function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  const base = import.meta.env.VITE_API_BASE;
  if (base) return `${String(base).replace(/\/$/, "")}${p}`;
  if (typeof window !== "undefined") {
    const port = window.location.port;
    const host = window.location.hostname;
    if (port === "5173" || port === "4173") {
      return `http://${host}:8000${p}`;
    }
  }
  return p;
}

async function loadAll() {
  loading.value = true;
  err.value = "";
  resetMsg.value = "";
  try {
    const [s, r] = await Promise.all([
      fetch(apiUrl("/api/analytics/summary")),
      fetch(apiUrl("/api/analytics/voice-turns?limit=40")),
    ]);
    if (!s.ok) throw new Error(await s.text());
    if (!r.ok) throw new Error(await r.text());
    summary.value = await s.json();
    const rd = await r.json();
    recent.value = rd.items || [];
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
    summary.value = null;
    recent.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void loadAll();
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

function fmtNsMs(ns) {
  if (ns == null || ns === "") return "—";
  const n = Number(ns);
  if (Number.isNaN(n)) return "—";
  return `${(n / 1e6).toFixed(1)} ms`;
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

const cards = computed(() => {
  const s = summary.value;
  if (!s) return [];
  return [
    { label: "Total questions (voice)", value: fmtNum(s.total_questions, 0) },
    { label: "Avg end-to-end latency", value: fmtMs(s.avg_total_ms) },
    { label: "Avg Ollama wall time", value: fmtMs(s.avg_ollama_wall_ms) },
    { label: "Min / max latency", value: `${fmtMs(s.min_total_ms)} / ${fmtMs(s.max_total_ms)}` },
    { label: "Prompt tokens (sum)", value: fmtNum(s.sum_prompt_tokens, 0) },
    { label: "Completion tokens (sum)", value: fmtNum(s.sum_completion_tokens, 0) },
    { label: "Total tokens (sum)", value: fmtNum(s.sum_total_tokens, 0) },
    { label: "Avg heard / answer chars", value: `${fmtNum(s.avg_heard_chars, 1)} / ${fmtNum(s.avg_answer_chars, 1)}` },
  ];
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
    await loadAll();
  } catch (e) {
    resetMsg.value = e instanceof Error ? e.message : String(e);
  }
}
</script>

<template>
  <div class="dash">
    <router-link to="/" class="dash__back">← Live</router-link>
    <div class="dash__head">
      <h1 class="dash__title">Analytics</h1>
      <p class="dash__sub">
        Metrics from mic → <code>/api/voice-turn</code> → Ollama (stored locally on the API server).
      </p>
      <div class="dash__actions">
        <button type="button" class="btn" :disabled="loading" @click="loadAll">
          {{ loading ? "Loading…" : "Refresh" }}
        </button>
      </div>
    </div>

    <p v-if="err" class="dash__err" role="alert">{{ err }}</p>

    <div v-if="!err && summary" class="dash__grid">
      <article v-for="(c, i) in cards" :key="i" class="card">
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
              <th>Latency</th>
              <th>Ollama (wall)</th>
              <th>Ollama (server)</th>
              <th>Tokens (p / c)</th>
              <th>Heard / answer chars</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in recent" :key="row.id">
              <td>{{ row.id }}</td>
              <td class="mono">{{ fmtTs(row.ts) }}</td>
              <td class="mono">{{ fmtMs(row.total_request_ms) }}</td>
              <td class="mono">{{ fmtMs(row.ollama_wall_ms) }}</td>
              <td class="mono">{{ fmtNsMs(row.ollama_total_duration_ns) }}</td>
              <td class="mono">
                {{ row.prompt_tokens ?? "—" }} / {{ row.completion_tokens ?? "—" }}
              </td>
              <td class="mono">{{ row.heard_chars }} / {{ row.answer_chars }}</td>
            </tr>
            <tr v-if="!recent.length && !loading">
              <td colspan="7" class="table__empty">No data yet — use the mic on the Live page.</td>
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
  background: #e4e2e2;
}

.dash__back {
  display: inline-block;
  margin-bottom: 1rem;
  font-size: clamp(0.9rem, 1.3vw, 1rem);
  font-weight: 600;
  color: #0d6f6f;
  text-decoration: none;
}

.dash__back:hover {
  text-decoration: underline;
}

.dash__head {
  margin-bottom: 1.5rem;
}

.dash__title {
  margin: 0 0 0.35rem;
  font-size: clamp(1.5rem, 2.5vw, 2.25rem);
  font-weight: 700;
  color: #1a1a1a;
}

.dash__sub {
  margin: 0 0 1rem;
  font-size: clamp(0.9rem, 1.4vw, 1.05rem);
  color: #444;
  max-width: 48rem;
}

.dash__sub code {
  font-size: 0.9em;
  background: rgba(0, 0, 0, 0.06);
  padding: 0.1em 0.35em;
  border-radius: 4px;
}

.dash__actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.dash__err {
  padding: 0.75rem 1rem;
  border-radius: 10px;
  background: rgba(176, 0, 32, 0.08);
  color: #8b0000;
  margin: 0 0 1rem;
}

.dash__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
  gap: 1rem;
  margin-bottom: 2rem;
}

.card {
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 14px;
  padding: 1.1rem 1.25rem;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
}

.card--wide {
  grid-column: 1 / -1;
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
  background: #fff;
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
</style>
