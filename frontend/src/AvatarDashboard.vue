<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";

const languages = ["Auto", "Chinese", "English", "German", "Italian", "Portuguese", "Spanish", "Japanese", "Korean", "French", "Russian"];

const avatarVoiceName = ref("Avatar Voice");
const promptText = ref("");
const customerRecordFile = ref(null);
const customerRecordPreview = ref("");
const customerSaveUrl = ref("");
const customerStatus = ref("");
const customerRecording = ref(false);
const customerRecordBusy = ref(false);
const customerCloneBusy = ref(false);

const voicesAvailable = ref([]);
const selectedVoiceUrl = ref("");
const selectedVoiceFile = ref(null);
const voiceProfileUrl = ref("");
const voiceProfileStatus = ref("");

const generateText = ref("Hello, good morning. How are you?");
const generateLang = ref("English");
const generateBusy = ref(false);
const generateStatus = ref("");
const generateAudioUrl = ref("");

const voiceCount = computed(() => voicesAvailable.value.length);
const hasSavedRecording = computed(() => Boolean(customerSaveUrl.value));
const hasVoiceProfile = computed(() => Boolean(voiceProfileUrl.value));
const hasVoiceFileReady = computed(() => Boolean(selectedVoiceFile.value));

const ragFileInput = ref(null);
const ragChunkCount = ref(null);
const ragChromaPath = ref("");
const ragSources = ref([]);
const ragUploadBusy = ref(false);
const ragPanelMsg = ref("");
const ragQueryText = ref("");
const ragQueryBusy = ref(false);
const ragQueryHits = ref([]);

const spConfig = ref(null);
const spSyncBusy = ref(false);
const spSyncSummary = ref("");

const gdriveConfig = ref(null);
const gdriveSyncBusy = ref(false);
const gdriveSyncSummary = ref("");

const dropboxConfig = ref(null);
const dropboxSyncBusy = ref(false);
const dropboxSyncSummary = ref("");

/** Avatar voice hub: which step panel is open (1–3) */
const openVoiceStudioStep = ref(null);

/** Knowledge hub: grid + detail panel */
const activeKbSource = ref(null);
const studioSummary = ref(null);
const spForm = ref({
  azure_tenant_id: "",
  azure_client_id: "",
  azure_client_secret: "",
  sharepoint_site_url: "",
  sharepoint_folder_path: "",
});
const spConnectBusy = ref(false);
const spConnectStatus = ref("");
const gdriveForm = ref({ folder_id: "", credentials_path: "" });
const gdriveCredFileInput = ref(null);
const gdriveConnectBusy = ref(false);
const gdriveConnectStatus = ref("");
const dbxForm = ref({
  dropbox_access_token: "",
  dropbox_refresh_token: "",
  dropbox_app_key: "",
  dropbox_app_secret: "",
  dropbox_folder_path: "",
});
const dbxConnectBusy = ref(false);
const dbxConnectStatus = ref("");

const s3Config = ref(null);
const s3SyncBusy = ref(false);
const s3SyncSummary = ref("");
const s3Form = ref({
  s3_bucket: "",
  s3_prefix: "",
  aws_region: "",
  aws_access_key_id: "",
  aws_secret_access_key: "",
  aws_session_token: "",
  s3_use_default_credential_chain: "",
});
const s3ConnectBusy = ref(false);
const s3ConnectStatus = ref("");

const azureBlobConfig = ref(null);
const azureBlobSyncBusy = ref(false);
const azureBlobSyncSummary = ref("");
const azureForm = ref({
  azure_storage_connection_string: "",
  azure_storage_account_name: "",
  azure_storage_account_key: "",
  azure_blob_container: "",
  azure_blob_prefix: "",
});
const azureConnectBusy = ref(false);
const azureConnectStatus = ref("");

const gcsConfig = ref(null);
const gcsSyncBusy = ref(false);
const gcsSyncSummary = ref("");
const gcsForm = ref({ bucket: "", prefix: "", credentials_path: "", use_adc: false });
const gcsCredFileInput = ref(null);
const gcsConnectBusy = ref(false);
const gcsConnectStatus = ref("");

/** LLM provider hub (local, OpenAI, Anthropic, Google Gemini) */
const activeLlmSource = ref(null);
const llmConfig = ref(null);
const llmForm = ref({
  ollama_base: "",
  ollama_model: "",
  model_api_style: "auto",
  openai_api_key: "",
  openai_base_url: "",
  openai_model: "",
  anthropic_api_key: "",
  anthropic_base_url: "",
  anthropic_model: "",
  google_api_key: "",
  google_gemini_base_url: "",
  google_gemini_model: "",
});
const llmSaveBusy = ref(false);
const llmSaveStatus = ref("");

/** Vector database hub (Pinecone, Milvus) — credentials for future ingest / RAG backends */
const activeVectorDbSource = ref(null);
const pineconeForm = ref({
  pinecone_api_key: "",
  pinecone_index_name: "",
  pinecone_host: "",
});
const milvusForm = ref({
  milvus_uri: "",
  milvus_token: "",
  milvus_db_name: "",
  milvus_collection_name: "",
});
const pineconeConnectBusy = ref(false);
const pineconeConnectStatus = ref("");
const milvusConnectBusy = ref(false);
const milvusConnectStatus = ref("");

const weaviateForm = ref({
  weaviate_url: "",
  weaviate_api_key: "",
  weaviate_class_name: "",
});
const qdrantForm = ref({
  qdrant_url: "",
  qdrant_api_key: "",
  qdrant_collection_name: "",
});
const elasticsearchForm = ref({
  elasticsearch_url: "",
  elasticsearch_api_key: "",
  elasticsearch_index_name: "",
});
const weaviateConnectBusy = ref(false);
const weaviateConnectStatus = ref("");
const qdrantConnectBusy = ref(false);
const qdrantConnectStatus = ref("");
const elasticsearchConnectBusy = ref(false);
const elasticsearchConnectStatus = ref("");

const azureAiSearchForm = ref({
  azure_ai_search_endpoint: "",
  azure_ai_search_api_key: "",
  azure_ai_search_index_name: "",
});
const azureAiSearchConnectBusy = ref(false);
const azureAiSearchConnectStatus = ref("");

let mediaRecorder = null;
let mediaStream = null;
let recordChunks = [];
const objectUrls = [];

function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  const base = import.meta.env.VITE_API_BASE;
  if (base) return `${String(base).replace(/\/$/, "")}${p}`;
  if (typeof window !== "undefined") {
    const port = window.location.port;
    const host = window.location.hostname;
    if (port === "5173" || port === "4173") return `http://${host}:8000${p}`;
  }
  return p;
}

function toPreview(file) {
  if (!file) return "";
  const url = URL.createObjectURL(file);
  objectUrls.push(url);
  return url;
}

async function postForm(path, fd) {
  const res = await fetch(apiUrl(path), { method: "POST", body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `Request failed (${res.status})`);
  return data;
}

function cleanStatusText(v) {
  return String(v || "").replace("Finished. (生成完成)", "").replace("Finished.", "").replace("(生成完成)", "").trim();
}

async function loadConfig() {
  try {
    const res = await fetch(apiUrl("/api/avatar/config"));
    if (!res.ok) throw new Error("Unable to load avatar config.");
    const data = await res.json();
    promptText.value = String(data.sample_text || "").trim();
  } catch {
    promptText.value = "Please read this script clearly while recording your voice.";
  }
}

async function loadSharepointConfig() {
  try {
    const res = await fetch(apiUrl("/api/sharepoint/config"));
    if (!res.ok) {
      spConfig.value = { configured: false };
      return;
    }
    spConfig.value = await res.json();
  } catch {
    spConfig.value = { configured: false };
  }
}

async function syncSharePoint() {
  spSyncBusy.value = true;
  spSyncSummary.value = "";
  try {
    const res = await fetch(apiUrl("/api/sharepoint/sync"), { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      const msg =
        typeof d === "string"
          ? d
          : Array.isArray(d)
            ? d.map((x) => x.msg || String(x)).join(" ")
            : JSON.stringify(d);
      throw new Error(msg || `Sync failed (${res.status})`);
    }
    const n = data.total_ingested ?? 0;
    const errN = Array.isArray(data.errors) ? data.errors.length : 0;
    spSyncSummary.value =
      errN > 0
        ? `Ingested ${n} file(s). ${errN} error(s) — check API logs.`
        : `Ingested ${n} file(s) from SharePoint into ChromaDB.`;
    await loadRagStatus();
    await loadSharepointConfig();
  } catch (e) {
    spSyncSummary.value = e instanceof Error ? e.message : String(e);
  } finally {
    spSyncBusy.value = false;
  }
}

async function loadGoogleDriveConfig() {
  try {
    const res = await fetch(apiUrl("/api/google-drive/config"));
    if (!res.ok) {
      gdriveConfig.value = { configured: false };
      return;
    }
    gdriveConfig.value = await res.json();
  } catch {
    gdriveConfig.value = { configured: false };
  }
}

async function syncGoogleDrive() {
  gdriveSyncBusy.value = true;
  gdriveSyncSummary.value = "";
  try {
    const res = await fetch(apiUrl("/api/google-drive/sync"), { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      const msg =
        typeof d === "string"
          ? d
          : Array.isArray(d)
            ? d.map((x) => x.msg || String(x)).join(" ")
            : JSON.stringify(d);
      throw new Error(msg || `Sync failed (${res.status})`);
    }
    const n = data.total_ingested ?? 0;
    const errN = Array.isArray(data.errors) ? data.errors.length : 0;
    gdriveSyncSummary.value =
      errN > 0
        ? `Ingested ${n} file(s). ${errN} error(s) — check API logs.`
        : `Ingested ${n} file(s) from Google Drive into ChromaDB.`;
    await loadRagStatus();
    await loadGoogleDriveConfig();
  } catch (e) {
    gdriveSyncSummary.value = e instanceof Error ? e.message : String(e);
  } finally {
    gdriveSyncBusy.value = false;
  }
}

async function loadDropboxConfig() {
  try {
    const res = await fetch(apiUrl("/api/dropbox/config"));
    if (!res.ok) {
      dropboxConfig.value = { configured: false };
      return;
    }
    dropboxConfig.value = await res.json();
  } catch {
    dropboxConfig.value = { configured: false };
  }
}

function toggleKbSource(id) {
  activeKbSource.value = activeKbSource.value === id ? null : id;
}

function openVoiceHubStep(step) {
  if (step !== 1 && step !== 2 && step !== 3) return;
  if (openVoiceStudioStep.value === step) {
    openVoiceStudioStep.value = null;
    return;
  }
  openVoiceStudioStep.value = step;
  nextTick(() => {
    const el = document.getElementById(`studio-voice-step${step}`);
    el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
}

function toggleLlmSource(id) {
  activeLlmSource.value = activeLlmSource.value === id ? null : id;
}

function toggleVectorDbSource(id) {
  activeVectorDbSource.value = activeVectorDbSource.value === id ? null : id;
}

async function loadStudioSummary() {
  try {
    const res = await fetch(apiUrl("/api/studio/integrations"));
    if (!res.ok) {
      studioSummary.value = null;
      return;
    }
    studioSummary.value = await res.json();
  } catch {
    studioSummary.value = null;
  }
}

async function loadLlmConfig() {
  try {
    const res = await fetch(apiUrl("/api/llm/config"));
    if (!res.ok) {
      llmConfig.value = null;
      return;
    }
    const data = await res.json();
    llmConfig.value = data;
    if (data.local) {
      llmForm.value.ollama_base = String(data.local.ollama_base || "");
      llmForm.value.ollama_model = String(data.local.ollama_model || "");
      llmForm.value.model_api_style = String(data.local.model_api_style || "auto");
    }
    if (data.openai) {
      llmForm.value.openai_base_url = String(data.openai.base_url || "");
      llmForm.value.openai_model = String(data.openai.model || "");
    }
    if (data.anthropic) {
      llmForm.value.anthropic_base_url = String(data.anthropic.base_url || "");
      llmForm.value.anthropic_model = String(data.anthropic.model || "");
    }
    if (data.google) {
      llmForm.value.google_gemini_base_url = String(data.google.base_url || "");
      llmForm.value.google_gemini_model = String(data.google.model || "");
    }
  } catch {
    llmConfig.value = null;
  }
}

async function connectLlmStudio(provider) {
  llmSaveBusy.value = true;
  llmSaveStatus.value = "";
  try {
    const res = await fetch(apiUrl("/api/studio/integrations/llm"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        llm_provider: provider,
        ollama_base: llmForm.value.ollama_base,
        ollama_model: llmForm.value.ollama_model,
        model_api_style: llmForm.value.model_api_style,
        openai_api_key: llmForm.value.openai_api_key,
        openai_base_url: llmForm.value.openai_base_url,
        openai_model: llmForm.value.openai_model,
        anthropic_api_key: llmForm.value.anthropic_api_key,
        anthropic_base_url: llmForm.value.anthropic_base_url,
        anthropic_model: llmForm.value.anthropic_model,
        google_api_key: llmForm.value.google_api_key,
        google_gemini_base_url: llmForm.value.google_gemini_base_url,
        google_gemini_model: llmForm.value.google_gemini_model,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    llmSaveStatus.value = "Saved. Voice and lip-sync use this provider immediately on the server.";
    llmForm.value.openai_api_key = "";
    llmForm.value.anthropic_api_key = "";
    llmForm.value.google_api_key = "";
    await loadLlmConfig();
    await loadStudioSummary();
  } catch (e) {
    llmSaveStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    llmSaveBusy.value = false;
  }
}

async function connectSharePointStudio() {
  spConnectBusy.value = true;
  spConnectStatus.value = "";
  try {
    const res = await fetch(apiUrl("/api/studio/integrations/sharepoint"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        azure_tenant_id: spForm.value.azure_tenant_id,
        azure_client_id: spForm.value.azure_client_id,
        azure_client_secret: spForm.value.azure_client_secret,
        sharepoint_site_url: spForm.value.sharepoint_site_url,
        sharepoint_folder_path: spForm.value.sharepoint_folder_path,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    spConnectStatus.value = "Saved. Credentials apply on this server immediately.";
    await loadSharepointConfig();
    await loadStudioSummary();
  } catch (e) {
    spConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    spConnectBusy.value = false;
  }
}

async function connectGoogleDriveStudio() {
  gdriveConnectBusy.value = true;
  gdriveConnectStatus.value = "";
  try {
    const fd = new FormData();
    fd.append("folder_id", gdriveForm.value.folder_id);
    fd.append("credentials_path", gdriveForm.value.credentials_path);
    const el = gdriveCredFileInput.value;
    if (el?.files?.[0]) {
      fd.append("credentials", el.files[0]);
    }
    const res = await fetch(apiUrl("/api/studio/integrations/google-drive"), {
      method: "POST",
      body: fd,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    gdriveConnectStatus.value = "Saved. You can sync when ready.";
    if (el) el.value = "";
    await loadGoogleDriveConfig();
    await loadStudioSummary();
  } catch (e) {
    gdriveConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    gdriveConnectBusy.value = false;
  }
}

async function connectDropboxStudio() {
  dbxConnectBusy.value = true;
  dbxConnectStatus.value = "";
  try {
    const res = await fetch(apiUrl("/api/studio/integrations/dropbox"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dropbox_access_token: dbxForm.value.dropbox_access_token,
        dropbox_refresh_token: dbxForm.value.dropbox_refresh_token,
        dropbox_app_key: dbxForm.value.dropbox_app_key,
        dropbox_app_secret: dbxForm.value.dropbox_app_secret,
        dropbox_folder_path: dbxForm.value.dropbox_folder_path,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    dbxConnectStatus.value = "Saved. You can sync when ready.";
    await loadDropboxConfig();
    await loadStudioSummary();
  } catch (e) {
    dbxConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    dbxConnectBusy.value = false;
  }
}

async function connectPineconeStudio() {
  pineconeConnectBusy.value = true;
  pineconeConnectStatus.value = "";
  try {
    const res = await fetch(apiUrl("/api/studio/integrations/pinecone"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(pineconeForm.value),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    pineconeConnectStatus.value = "Saved.";
    await loadStudioSummary();
  } catch (e) {
    pineconeConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    pineconeConnectBusy.value = false;
  }
}

async function connectMilvusStudio() {
  milvusConnectBusy.value = true;
  milvusConnectStatus.value = "";
  try {
    const res = await fetch(apiUrl("/api/studio/integrations/milvus"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(milvusForm.value),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    milvusConnectStatus.value = "Saved.";
    await loadStudioSummary();
  } catch (e) {
    milvusConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    milvusConnectBusy.value = false;
  }
}

async function connectWeaviateStudio() {
  weaviateConnectBusy.value = true;
  weaviateConnectStatus.value = "";
  try {
    const res = await fetch(apiUrl("/api/studio/integrations/weaviate"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(weaviateForm.value),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    weaviateConnectStatus.value = "Saved.";
    await loadStudioSummary();
  } catch (e) {
    weaviateConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    weaviateConnectBusy.value = false;
  }
}

async function connectQdrantStudio() {
  qdrantConnectBusy.value = true;
  qdrantConnectStatus.value = "";
  try {
    const res = await fetch(apiUrl("/api/studio/integrations/qdrant"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(qdrantForm.value),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    qdrantConnectStatus.value = "Saved.";
    await loadStudioSummary();
  } catch (e) {
    qdrantConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    qdrantConnectBusy.value = false;
  }
}

async function connectElasticsearchStudio() {
  elasticsearchConnectBusy.value = true;
  elasticsearchConnectStatus.value = "";
  try {
    const res = await fetch(apiUrl("/api/studio/integrations/elasticsearch"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(elasticsearchForm.value),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    elasticsearchConnectStatus.value = "Saved.";
    await loadStudioSummary();
  } catch (e) {
    elasticsearchConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    elasticsearchConnectBusy.value = false;
  }
}

async function connectAzureAiSearchStudio() {
  azureAiSearchConnectBusy.value = true;
  azureAiSearchConnectStatus.value = "";
  try {
    const res = await fetch(apiUrl("/api/studio/integrations/azure-ai-search"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(azureAiSearchForm.value),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    azureAiSearchConnectStatus.value = "Saved.";
    await loadStudioSummary();
  } catch (e) {
    azureAiSearchConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    azureAiSearchConnectBusy.value = false;
  }
}

async function clearStudioIntegration(section) {
  const labels = {
    sharepoint: "SharePoint",
    google_drive: "Google Drive",
    dropbox: "Dropbox",
    s3: "Amazon S3",
    azure_blob: "Azure Blob Storage",
    gcs: "Google Cloud Storage",
    llm: "LLM provider",
    pinecone: "Pinecone",
    milvus: "Milvus",
    weaviate: "Weaviate",
    qdrant: "Qdrant",
    elasticsearch: "Elasticsearch",
    azure_ai_search: "Azure AI Search",
  };
  if (!window.confirm(`Remove saved ${labels[section] || section} settings from this server?`)) {
    return;
  }
  try {
    const res = await fetch(apiUrl(`/api/studio/integrations/${section}`), { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || "Clear failed");
    }
    await loadStudioSummary();
    await loadSharepointConfig();
    await loadGoogleDriveConfig();
    await loadDropboxConfig();
    await loadS3Config();
    await loadAzureBlobConfig();
    await loadGcsConfig();
    await loadLlmConfig();
  } catch (e) {
    window.alert(e instanceof Error ? e.message : String(e));
  }
}

async function syncDropbox() {
  dropboxSyncBusy.value = true;
  dropboxSyncSummary.value = "";
  try {
    const res = await fetch(apiUrl("/api/dropbox/sync"), { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      const msg =
        typeof d === "string"
          ? d
          : Array.isArray(d)
            ? d.map((x) => x.msg || String(x)).join(" ")
            : JSON.stringify(d);
      throw new Error(msg || `Sync failed (${res.status})`);
    }
    const n = data.total_ingested ?? 0;
    const errN = Array.isArray(data.errors) ? data.errors.length : 0;
    dropboxSyncSummary.value =
      errN > 0
        ? `Ingested ${n} file(s). ${errN} error(s) — check API logs.`
        : `Ingested ${n} file(s) from Dropbox into ChromaDB.`;
    await loadRagStatus();
    await loadDropboxConfig();
  } catch (e) {
    dropboxSyncSummary.value = e instanceof Error ? e.message : String(e);
  } finally {
    dropboxSyncBusy.value = false;
  }
}

async function loadS3Config() {
  try {
    const res = await fetch(apiUrl("/api/s3/config"));
    if (!res.ok) {
      s3Config.value = { configured: false };
      return;
    }
    s3Config.value = await res.json();
  } catch {
    s3Config.value = { configured: false };
  }
}

async function syncS3() {
  s3SyncBusy.value = true;
  s3SyncSummary.value = "";
  try {
    const res = await fetch(apiUrl("/api/s3/sync"), { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    const n = data.total_ingested ?? 0;
    const errN = Array.isArray(data.errors) ? data.errors.length : 0;
    s3SyncSummary.value =
      errN > 0
        ? `Ingested ${n} file(s). ${errN} error(s) — check API logs.`
        : `Ingested ${n} file(s) from S3 into ChromaDB.`;
    await loadRagStatus();
    await loadS3Config();
  } catch (e) {
    s3SyncSummary.value = e instanceof Error ? e.message : String(e);
  } finally {
    s3SyncBusy.value = false;
  }
}

async function connectS3Studio() {
  s3ConnectBusy.value = true;
  s3ConnectStatus.value = "";
  try {
    const res = await fetch(apiUrl("/api/studio/integrations/s3"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(s3Form.value),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    s3ConnectStatus.value = "Saved.";
    await loadS3Config();
    await loadStudioSummary();
  } catch (e) {
    s3ConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    s3ConnectBusy.value = false;
  }
}

async function loadAzureBlobConfig() {
  try {
    const res = await fetch(apiUrl("/api/azure-blob/config"));
    if (!res.ok) {
      azureBlobConfig.value = { configured: false };
      return;
    }
    azureBlobConfig.value = await res.json();
  } catch {
    azureBlobConfig.value = { configured: false };
  }
}

async function syncAzureBlob() {
  azureBlobSyncBusy.value = true;
  azureBlobSyncSummary.value = "";
  try {
    const res = await fetch(apiUrl("/api/azure-blob/sync"), { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    const n = data.total_ingested ?? 0;
    const errN = Array.isArray(data.errors) ? data.errors.length : 0;
    azureBlobSyncSummary.value =
      errN > 0
        ? `Ingested ${n} file(s). ${errN} error(s) — check API logs.`
        : `Ingested ${n} file(s) from Azure Blob into ChromaDB.`;
    await loadRagStatus();
    await loadAzureBlobConfig();
  } catch (e) {
    azureBlobSyncSummary.value = e instanceof Error ? e.message : String(e);
  } finally {
    azureBlobSyncBusy.value = false;
  }
}

async function connectAzureBlobStudio() {
  azureConnectBusy.value = true;
  azureConnectStatus.value = "";
  try {
    const res = await fetch(apiUrl("/api/studio/integrations/azure-blob"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(azureForm.value),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    azureConnectStatus.value = "Saved.";
    await loadAzureBlobConfig();
    await loadStudioSummary();
  } catch (e) {
    azureConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    azureConnectBusy.value = false;
  }
}

async function loadGcsConfig() {
  try {
    const res = await fetch(apiUrl("/api/gcs/config"));
    if (!res.ok) {
      gcsConfig.value = { configured: false };
      return;
    }
    gcsConfig.value = await res.json();
  } catch {
    gcsConfig.value = { configured: false };
  }
}

async function syncGcs() {
  gcsSyncBusy.value = true;
  gcsSyncSummary.value = "";
  try {
    const res = await fetch(apiUrl("/api/gcs/sync"), { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    const n = data.total_ingested ?? 0;
    const errN = Array.isArray(data.errors) ? data.errors.length : 0;
    gcsSyncSummary.value =
      errN > 0
        ? `Ingested ${n} file(s). ${errN} error(s) — check API logs.`
        : `Ingested ${n} file(s) from GCS into ChromaDB.`;
    await loadRagStatus();
    await loadGcsConfig();
  } catch (e) {
    gcsSyncSummary.value = e instanceof Error ? e.message : String(e);
  } finally {
    gcsSyncBusy.value = false;
  }
}

async function connectGcsStudio() {
  gcsConnectBusy.value = true;
  gcsConnectStatus.value = "";
  try {
    const fd = new FormData();
    fd.append("bucket", gcsForm.value.bucket);
    fd.append("prefix", gcsForm.value.prefix);
    fd.append("credentials_path", gcsForm.value.credentials_path);
    fd.append("gcs_use_adc", gcsForm.value.use_adc ? "1" : "");
    const el = gcsCredFileInput.value;
    if (el?.files?.[0]) {
      fd.append("credentials", el.files[0]);
    }
    const res = await fetch(apiUrl("/api/studio/integrations/gcs"), { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    gcsConnectStatus.value = "Saved.";
    if (el) el.value = "";
    await loadGcsConfig();
    await loadStudioSummary();
  } catch (e) {
    gcsConnectStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    gcsConnectBusy.value = false;
  }
}

async function loadRagStatus() {
  try {
    const res = await fetch(apiUrl("/api/rag/status"));
    if (!res.ok) throw new Error("RAG unavailable");
    const data = await res.json();
    ragChunkCount.value = typeof data.chunk_count === "number" ? data.chunk_count : null;
    ragChromaPath.value = String(data.chroma_path || "");
    ragSources.value = Array.isArray(data.sources) ? data.sources : [];
  } catch {
    ragChunkCount.value = null;
    ragChromaPath.value = "";
    ragSources.value = [];
  }
}

async function uploadRagDocuments() {
  const el = ragFileInput.value;
  if (!el?.files?.length) {
    ragPanelMsg.value = "Choose one or more PDF, DOCX, or TXT files.";
    return;
  }
  ragUploadBusy.value = true;
  ragPanelMsg.value = "";
  try {
    const fd = new FormData();
    for (const f of el.files) {
      fd.append("files", f);
    }
    const res = await fetch(apiUrl("/api/rag/ingest"), { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    const lines = [];
    for (const r of data.results || []) {
      if (r.ok) {
        lines.push(`${r.filename}: ${r.chunks_added} chunks indexed`);
      } else {
        lines.push(`${r.filename}: ${r.error || "failed"}`);
      }
    }
    ragPanelMsg.value = lines.length ? lines.join(" · ") : "Done.";
    el.value = "";
    await loadRagStatus();
  } catch (e) {
    ragPanelMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    ragUploadBusy.value = false;
  }
}

async function deleteRagSource(sourceId) {
  if (!sourceId || !window.confirm("Remove this document from the knowledge base?")) {
    return;
  }
  ragPanelMsg.value = "";
  try {
    const res = await fetch(apiUrl(`/api/rag/sources/${encodeURIComponent(sourceId)}`), { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    ragPanelMsg.value = `Removed ${data.deleted_chunks ?? 0} chunks.`;
    await loadRagStatus();
  } catch (e) {
    ragPanelMsg.value = e instanceof Error ? e.message : String(e);
  }
}

async function runRagQuery() {
  const q = ragQueryText.value.trim();
  if (!q) return;
  ragQueryBusy.value = true;
  ragQueryHits.value = [];
  try {
    const res = await fetch(apiUrl("/api/rag/query"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q, n_results: 8 }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d));
    }
    ragQueryHits.value = Array.isArray(data.results) ? data.results : [];
    if (!ragQueryHits.value.length) {
      ragPanelMsg.value = "No matching chunks (ingest documents first).";
    }
  } catch (e) {
    ragPanelMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    ragQueryBusy.value = false;
  }
}

async function loadVoices() {
  try {
    const res = await fetch(apiUrl("/api/avatar/voices"));
    if (!res.ok) throw new Error("Unable to load voices.");
    const data = await res.json();
    voicesAvailable.value = Array.isArray(data.items) ? data.items : [];
    if (!selectedVoiceUrl.value && voicesAvailable.value.length) selectedVoiceUrl.value = voicesAvailable.value[0].url;
  } catch {
    voicesAvailable.value = [];
  }
}

async function startRecording() {
  customerStatus.value = "";
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported?.("audio/webm;codecs=opus") && "audio/webm;codecs=opus") || "audio/webm";
    mediaRecorder = new MediaRecorder(mediaStream, { mimeType: mime });
    recordChunks = [];
    mediaRecorder.ondataavailable = (ev) => {
      if (ev.data && ev.data.size > 0) recordChunks.push(ev.data);
    };
    mediaRecorder.onstop = () => {
      const blob = new Blob(recordChunks, { type: mediaRecorder?.mimeType || "audio/webm" });
      const file = new File([blob], "avatar_voice_recording.webm", { type: blob.type });
      customerRecordFile.value = file;
      customerRecordPreview.value = toPreview(file);
      customerRecording.value = false;
      if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
      mediaStream = null;
      mediaRecorder = null;
      customerStatus.value = "Recording ready. Save it to continue.";
    };
    mediaRecorder.start();
    customerRecording.value = true;
    customerStatus.value = "Recording...";
  } catch (e) {
    customerStatus.value = e instanceof Error ? e.message : "Microphone access failed.";
  }
}

function stopRecording() {
  if (mediaRecorder && customerRecording.value) mediaRecorder.stop();
}

async function saveRecording() {
  if (!customerRecordFile.value) return void (customerStatus.value = "Please record audio first.");
  customerRecordBusy.value = true;
  customerStatus.value = "Saving recording...";
  try {
    const fd = new FormData();
    fd.append("audio", customerRecordFile.value);
    fd.append("customer_name", avatarVoiceName.value || "Avatar Voice");
    fd.append("prompt_text", promptText.value || "");
    const out = await postForm("/api/avatar/save-customer-recording", fd);
    customerSaveUrl.value = apiUrl(out.audio_url || "");
    customerStatus.value = String(out.status || "Recording saved.");
  } catch (e) {
    customerStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    customerRecordBusy.value = false;
  }
}

async function cloneAndSaveVoice() {
  if (!customerSaveUrl.value) return void (customerStatus.value = "Save a recording first.");
  customerCloneBusy.value = true;
  voiceProfileStatus.value = "Creating reusable voice profile...";
  try {
    const res = await fetch(customerSaveUrl.value);
    if (!res.ok) throw new Error(`Unable to load saved recording (${res.status}).`);
    const blob = await res.blob();
    const refFile = new File([blob], blob.type.includes("wav") ? "avatar_source.wav" : "avatar_source.webm", { type: blob.type || "audio/webm" });
    const fd = new FormData();
    fd.append("ref_aud", refFile);
    fd.append("ref_txt", promptText.value || "Sample prompt text.");
    fd.append("use_xvec", "false");
    fd.append("voice_name", avatarVoiceName.value || "Avatar Voice");
    const out = await postForm("/api/avatar/save-voice", fd);
    voiceProfileUrl.value = apiUrl(out.voice_file_url || "");
    voiceProfileStatus.value = cleanStatusText(out.status) || "Voice profile ready.";
    await loadVoices();
  } catch (e) {
    voiceProfileStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    customerCloneBusy.value = false;
  }
}

async function useSelectedVoice() {
  if (!selectedVoiceUrl.value) return void (generateStatus.value = "Select a saved voice profile first.");
  try {
    const res = await fetch(apiUrl(selectedVoiceUrl.value));
    if (!res.ok) throw new Error(`Unable to fetch selected voice profile (${res.status}).`);
    const blob = await res.blob();
    selectedVoiceFile.value = new File([blob], "selected_voice_prompt.pt", { type: blob.type || "application/octet-stream" });
    generateStatus.value = "Voice profile selected.";
  } catch (e) {
    generateStatus.value = e instanceof Error ? e.message : String(e);
  }
}

async function generateVoice() {
  if (!selectedVoiceFile.value) return void (generateStatus.value = "Select a voice profile first.");
  if (!generateText.value.trim()) return void (generateStatus.value = "Target text is required.");
  generateBusy.value = true;
  generateStatus.value = "Generating audio...";
  try {
    const fd = new FormData();
    fd.append("file_obj", selectedVoiceFile.value);
    fd.append("text", generateText.value);
    fd.append("lang_disp", generateLang.value);
    const out = await postForm("/api/avatar/load-and-generate", fd);
    generateAudioUrl.value = apiUrl(out.audio_url || "");
    generateStatus.value = cleanStatusText(out.status) || "Done.";
  } catch (e) {
    generateStatus.value = e instanceof Error ? e.message : String(e);
  } finally {
    generateBusy.value = false;
  }
}

let syncSourcesPollId = null;

onMounted(() => {
  void loadConfig();
  void loadVoices();
  void loadRagStatus();
  void loadSharepointConfig();
  void loadGoogleDriveConfig();
  void loadDropboxConfig();
  void loadS3Config();
  void loadAzureBlobConfig();
  void loadGcsConfig();
  void loadStudioSummary();
  void loadLlmConfig();
  syncSourcesPollId = window.setInterval(() => {
    void loadSharepointConfig();
    void loadGoogleDriveConfig();
    void loadDropboxConfig();
    void loadS3Config();
    void loadAzureBlobConfig();
    void loadGcsConfig();
    void loadStudioSummary();
    void loadLlmConfig();
  }, 12000);
});

onUnmounted(() => {
  if (syncSourcesPollId != null) {
    window.clearInterval(syncSourcesPollId);
    syncSourcesPollId = null;
  }
  if (mediaRecorder && customerRecording.value) {
    try { mediaRecorder.stop(); } catch { /* ignore */ }
  }
  if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
  objectUrls.forEach((u) => URL.revokeObjectURL(u));
});

function formatIso(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return String(iso);
  }
}
</script>

<template>
  <div class="studio-page">
    <div class="studio-page__glow" aria-hidden="true" />
    <section class="studio">
      <header class="hero">
        <nav class="hero__nav">
          <router-link class="pill pill--ghost" to="/">Live hologram</router-link>
          <router-link class="pill pill--ghost" to="/analytics">Analytics</router-link>
        </nav>
        <div class="hero__title-row">
          <h1>Avatar Studio</h1>
          <span v-if="voiceCount" class="chip">{{ voiceCount }} voice{{ voiceCount === 1 ? "" : "s" }} on server</span>
        </div>
        <p class="hero__lede">
          Record against the sample script, clone a voice profile, synthesize speech — and plug in a knowledge base from
          your documents (ChromaDB RAG).
        </p>
      </header>

      <div class="studio-stack">
        <article class="flow-card flow-card--video-hub">
          <div class="flow-card__head flow-card__head--rag">
            <span class="step-badge step-badge--video">AV</span>
            <div>
              <h3>Avatar Video</h3>
              <p class="flow-card__sub">
                Lip-sync video workflows, presets, and exports — <strong>coming soon</strong>.
              </p>
            </div>
          </div>
          <div class="studio-hub-grid" role="list" aria-label="Avatar video (coming soon)">
            <div class="kb-tile kb-tile--disabled" role="listitem" aria-disabled="true">
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--video">Lip-sync</span>
                <span class="kb-tile__title">Templates &amp; timing</span>
                <span class="kb-tile__meta">Coming soon</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--video" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="2" y="5" width="20" height="14" rx="2" />
                  <path d="M10 9v6l5-3-5-3Z" />
                </svg>
              </span>
              <span class="kb-tile__soon">Soon</span>
            </div>
            <div class="kb-tile kb-tile--disabled" role="listitem" aria-disabled="true">
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--video">Framing</span>
                <span class="kb-tile__title">Crop &amp; safe areas</span>
                <span class="kb-tile__meta">Coming soon</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--video" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M4 7h16v10H4z" />
                  <path d="M9 7v10M15 7v10" />
                </svg>
              </span>
              <span class="kb-tile__soon">Soon</span>
            </div>
            <div class="kb-tile kb-tile--disabled" role="listitem" aria-disabled="true">
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--video">Batch</span>
                <span class="kb-tile__title">Queue &amp; export</span>
                <span class="kb-tile__meta">Coming soon</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--video" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 3v12" />
                  <path d="M8 15l4 4 4-4" />
                  <path d="M5 21h14" />
                </svg>
              </span>
              <span class="kb-tile__soon">Soon</span>
            </div>
          </div>
        </article>

        <article class="flow-card flow-card--voice-hub">
          <div class="flow-card__head flow-card__head--rag">
            <span class="step-badge step-badge--voice-hub">Vo</span>
            <div>
              <h3>Avatar voice</h3>
              <p class="flow-card__sub">Record a sample, create a voice profile, then generate speech — all in this panel.</p>
            </div>
          </div>
          <div class="studio-hub-grid" role="group" aria-label="Avatar voice shortcuts">
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': openVoiceStudioStep === 1 }"
              :aria-expanded="openVoiceStudioStep === 1"
              aria-controls="studio-voice-step1"
              @click="openVoiceHubStep(1)"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--voice-rec">Record</span>
                <span class="kb-tile__title">Mic &amp; script</span>
                <span class="kb-tile__meta">Capture audio</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--voice-rec" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Z" />
                  <path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4M8 22h8" />
                </svg>
              </span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': openVoiceStudioStep === 2 }"
              :aria-expanded="openVoiceStudioStep === 2"
              aria-controls="studio-voice-step2"
              @click="openVoiceHubStep(2)"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--voice-clone">Clone</span>
                <span class="kb-tile__title">Voice profile</span>
                <span class="kb-tile__meta">Build profile</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--voice-clone" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 3a7 7 0 1 0 7 7" />
                  <path d="M12 11a2 2 0 1 0 2 2" />
                  <path d="M5 21h6M16 16l5 5" />
                </svg>
              </span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': openVoiceStudioStep === 3 }"
              :aria-expanded="openVoiceStudioStep === 3"
              aria-controls="studio-voice-step3"
              @click="openVoiceHubStep(3)"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--voice-gen">Generate</span>
                <span class="kb-tile__title">Speech from profile</span>
                <span class="kb-tile__meta">Synthesize</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--voice-gen" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M9 18V5l12-2v13" />
                  <circle cx="6" cy="18" r="3" />
                  <circle cx="18" cy="16" r="3" />
                </svg>
              </span>
            </button>
          </div>

          <div class="voice-hub-panels">
            <p v-if="openVoiceStudioStep === null" class="voice-hub-panels__hint">
              Select <strong>Record</strong>, <strong>Clone</strong>, or <strong>Generate</strong> above to open that step.
            </p>
            <article
              v-show="openVoiceStudioStep === 1"
              id="studio-voice-step1"
              class="flow-card"
              :class="{ 'flow-card--live': customerRecording }"
            >
              <div class="flow-card__head">
                <span class="step-badge">1</span>
                <div>
                  <h3>Record avatar voice</h3>
                  <p class="flow-card__sub">Use a clear, steady read of the script below.</p>
                </div>
                <span v-if="customerRecording" class="rec-dot" aria-hidden="true"><span class="rec-dot__pulse" /></span>
              </div>

              <label class="field">
                <span>Avatar voice name</span>
                <input v-model="avatarVoiceName" type="text" placeholder="e.g. Concierge North" autocomplete="off" />
              </label>
              <label class="field">
                <span>Sample script</span>
                <textarea v-model="promptText" rows="5" placeholder="Script loads from server config…" />
              </label>

              <div class="actions">
                <button v-if="!customerRecording" type="button" class="btn" @click="startRecording">
                  <span class="btn__icon" aria-hidden="true">●</span>
                  Start recording
                </button>
                <button v-else type="button" class="btn btn--warn" @click="stopRecording">
                  <span class="btn__icon btn__icon--blink" aria-hidden="true">■</span>
                  Stop recording
                </button>
                <button
                  type="button"
                  class="btn btn--secondary"
                  :disabled="customerRecordBusy || !customerRecordFile"
                  @click="saveRecording"
                >
                  {{ customerRecordBusy ? "Saving…" : "Save recording" }}
                </button>
                <a
                  v-if="customerSaveUrl"
                  class="pill pill--link"
                  :href="customerSaveUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open saved file
                </a>
              </div>

              <div v-if="customerRecordPreview" class="audio-shell">
                <audio :src="customerRecordPreview" controls class="audio" />
              </div>
              <p v-if="customerStatus" class="status">{{ customerStatus }}</p>
              <p v-else class="status status--muted">Status appears here after you record or save.</p>
            </article>

            <article v-show="openVoiceStudioStep === 2" id="studio-voice-step2" class="flow-card">
              <div class="flow-card__head">
                <span class="step-badge">2</span>
                <div>
                  <h3>Create voice profile</h3>
                  <p class="flow-card__sub">Turns the saved recording into a <code>.pt</code> profile on the server.</p>
                </div>
              </div>

              <div class="actions">
                <button type="button" class="btn" :disabled="customerCloneBusy || !customerSaveUrl" @click="cloneAndSaveVoice">
                  {{ customerCloneBusy ? "Cloning…" : "Clone & save profile" }}
                </button>
                <a
                  v-if="voiceProfileUrl"
                  class="pill pill--link"
                  :href="voiceProfileUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Download profile
                </a>
              </div>
              <p v-if="voiceProfileStatus" class="status">{{ voiceProfileStatus }}</p>
              <p v-else class="status status--muted">Save a recording in step 1, then clone here.</p>

              <ul class="mini-check">
                <li :class="{ 'mini-check__on': hasSavedRecording }">Recording saved</li>
                <li :class="{ 'mini-check__on': hasVoiceProfile }">Profile file created</li>
              </ul>
            </article>

            <article v-show="openVoiceStudioStep === 3" id="studio-voice-step3" class="flow-card">
              <div class="flow-card__head">
                <span class="step-badge">3</span>
                <div>
                  <h3>Generate with saved voice</h3>
                  <p class="flow-card__sub">Pick a profile, load it, enter target text, then render audio.</p>
                </div>
              </div>

              <label class="field">
                <span>Voices available</span>
                <select v-model="selectedVoiceUrl">
                  <option value="">Select a saved voice…</option>
                  <option v-for="v in voicesAvailable" :key="`${v.url}-${v.name}`" :value="v.url">{{ v.name }}</option>
                </select>
              </label>
              <div class="actions">
                <button type="button" class="btn btn--secondary" :disabled="!selectedVoiceUrl" @click="useSelectedVoice">
                  Load selected profile
                </button>
                <span v-if="hasVoiceFileReady" class="chip chip--ok">Ready to generate</span>
              </div>

              <label class="field">
                <span>Target text</span>
                <textarea v-model="generateText" rows="4" placeholder="What should the avatar say?" />
              </label>
              <label class="field field--inline">
                <span>Language</span>
                <select v-model="generateLang">
                  <option v-for="l in languages" :key="l" :value="l">{{ l }}</option>
                </select>
              </label>

              <div class="actions">
                <button type="button" class="btn" :disabled="generateBusy" @click="generateVoice">
                  {{ generateBusy ? "Generating…" : "Generate speech" }}
                </button>
              </div>

              <div v-if="generateAudioUrl" class="audio-shell audio-shell--accent">
                <audio :src="generateAudioUrl" controls class="audio" />
              </div>
              <p v-if="generateStatus" class="status">{{ generateStatus }}</p>
              <p v-else class="status status--muted">Output audio appears here after generation.</p>
            </article>
          </div>
        </article>

        <article class="flow-card flow-card--kb-hub">
          <div class="flow-card__head flow-card__head--rag">
            <span class="step-badge step-badge--rag">KB</span>
            <div>
              <h3>Knowledge sources</h3>
              <p class="flow-card__sub">
                Pick a tile to upload files or enter cloud credentials. Everything feeds the same Chroma index for RAG.
              </p>
            </div>
          </div>

          <div class="kb-grid" role="group" aria-label="Knowledge source connectors">
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeKbSource === 'kb' }"
              @click="toggleKbSource('kb')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--chroma">ChromaDB</span>
                <span class="kb-tile__title">Local upload</span>
                <span class="kb-tile__meta">{{ ragChunkCount != null ? ragChunkCount + ' chunks' : 'No index yet' }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--chroma" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 3 3 7l9 4 9-4-9-4Z" />
                  <path d="m3 12 9 4 9-4M3 17l9 4 9-4" />
                </svg>
              </span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeKbSource === 'sharepoint' }"
              @click="toggleKbSource('sharepoint')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--sp">SharePoint</span>
                <span class="kb-tile__title">Microsoft 365</span>
                <span class="kb-tile__meta">{{ spConfig?.configured ? 'Connected' : 'Not connected' }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--sp" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M3 21h18" />
                  <path d="M5 21V7l7-4v18M12 7l7 4v14" />
                  <path d="M9 10v4M9 16v2M15 10v4M15 16v2" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.sharepoint?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeKbSource === 'gdrive' }"
              @click="toggleKbSource('gdrive')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--gd">Drive</span>
                <span class="kb-tile__title">Google Drive</span>
                <span class="kb-tile__meta">{{ gdriveConfig?.configured ? 'Connected' : 'Not connected' }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--gd" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2v11Z" />
                  <path d="m12 11 3-3" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.google_drive?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeKbSource === 'dropbox' }"
              @click="toggleKbSource('dropbox')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--db">Dropbox</span>
                <span class="kb-tile__title">Cloud folder</span>
                <span class="kb-tile__meta">{{ dropboxConfig?.configured ? 'Connected' : 'Not connected' }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--db" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 3 7 7l5 4 5-4-5-4Z" />
                  <path d="M7 7v8l5 4M7 7l5 4 5-4M17 7v8l-5 4" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.dropbox?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeKbSource === 's3' }"
              @click="toggleKbSource('s3')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--s3">S3</span>
                <span class="kb-tile__title">Amazon S3</span>
                <span class="kb-tile__meta">{{ s3Config?.configured ? 'Connected' : 'Not connected' }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--s3" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" />
                  <path d="M3.27 6.96 12 12.01l8.73-5.05M12 22.08V12" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.s3?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeKbSource === 'azure_blob' }"
              @click="toggleKbSource('azure_blob')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--azblob">Blob</span>
                <span class="kb-tile__title">Azure Blob</span>
                <span class="kb-tile__meta">{{ azureBlobConfig?.configured ? 'Connected' : 'Not connected' }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--azblob" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 2 20 6v12l-8 4-8-4V6l8-4Z" />
                  <ellipse cx="12" cy="12" rx="3" ry="2.25" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.azure_blob?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeKbSource === 'gcs' }"
              @click="toggleKbSource('gcs')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--gcs">GCS</span>
                <span class="kb-tile__title">Cloud Storage</span>
                <span class="kb-tile__meta">{{ gcsConfig?.configured ? 'Connected' : 'Not connected' }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--gcs" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10Z" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.gcs?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
          </div>

          <p v-if="!activeKbSource" class="kb-hint status status--muted">Select a source above to connect or upload.</p>

          <div v-else class="kb-detail">
            <div v-show="activeKbSource === 'kb'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Upload to ChromaDB</h4>
              <div class="rag-toolbar">
                <label class="rag-file">
                  <input
                    ref="ragFileInput"
                    type="file"
                    multiple
                    accept=".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
                  />
                  <span class="rag-file__btn">Choose files</span>
                  <span class="rag-file__hint">PDF · DOCX · TXT · MD</span>
                </label>
                <button type="button" class="btn btn--secondary" :disabled="ragUploadBusy" @click="uploadRagDocuments">
                  {{ ragUploadBusy ? "Indexing…" : "Ingest into ChromaDB" }}
                </button>
              </div>
              <p v-if="ragPanelMsg" class="status">{{ ragPanelMsg }}</p>
              <p v-else class="status status--muted">Indexed chunks persist under the API server data directory.</p>
              <p v-if="ragChromaPath" class="rag-path"><span class="rag-path__label">Store</span> {{ ragChromaPath }}</p>
              <div v-if="ragSources.length" class="rag-table-wrap">
                <table class="rag-table">
                  <thead>
                    <tr>
                      <th>Document</th>
                      <th>Chunks</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="s in ragSources" :key="s.source_id">
                      <td>{{ s.filename }}</td>
                      <td>{{ s.chunks }}</td>
                      <td>
                        <button type="button" class="btn-mini" @click="deleteRagSource(s.source_id)">Remove</button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="rag-query">
                <label class="field">
                  <span>Try a semantic search (debug)</span>
                  <textarea v-model="ragQueryText" rows="2" placeholder="Ask what your documents might answer…" />
                </label>
                <div class="actions">
                  <button type="button" class="btn btn--secondary" :disabled="ragQueryBusy || !ragQueryText.trim()" @click="runRagQuery">
                    {{ ragQueryBusy ? "Searching…" : "Search index" }}
                  </button>
                </div>
                <ul v-if="ragQueryHits.length" class="rag-hits">
                  <li v-for="(hit, idx) in ragQueryHits" :key="idx" class="rag-hit">
                    <span class="rag-hit__meta">{{ hit.metadata?.filename || "chunk" }} · #{{ hit.metadata?.chunk_index ?? "—" }}</span>
                    <p class="rag-hit__text">{{ hit.text }}</p>
                  </li>
                </ul>
              </div>
            </div>

            <div v-show="activeKbSource === 'sharepoint'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect SharePoint</h4>
              <div class="studio-form-grid">
                <label class="field">
                  <span>Directory (tenant) ID</span>
                  <input v-model="spForm.azure_tenant_id" type="text" autocomplete="off" />
                </label>
                <label class="field">
                  <span>Application (client) ID</span>
                  <input v-model="spForm.azure_client_id" type="text" autocomplete="off" />
                </label>
                <label class="field">
                  <span>Client secret</span>
                  <input v-model="spForm.azure_client_secret" type="password" autocomplete="off" />
                </label>
                <label class="field">
                  <span>SharePoint site URL</span>
                  <input v-model="spForm.sharepoint_site_url" type="text" placeholder="https://tenant.sharepoint.com/sites/..." />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Folder path (optional)</span>
                  <input v-model="spForm.sharepoint_folder_path" type="text" placeholder="Shared Documents/Handbook" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="spConnectBusy" @click="connectSharePointStudio">
                  {{ spConnectBusy ? "Saving…" : "Save & connect" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.sharepoint?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('sharepoint')"
                >
                  Clear saved
                </button>
              </div>
              <p v-if="spConnectStatus" class="status">{{ spConnectStatus }}</p>
              <p class="status status--muted kb-detail__fine">
                Grant Microsoft Graph <code>Sites.Read.All</code> (app permission) with admin consent. Values are stored in
                <code>backend/data/studio_integrations.json</code> on this server.
              </p>
              <template v-if="spConfig">
                <template v-if="spConfig.configured">
                  <p v-if="spConfig.live_sync_enabled" class="sp-live-line">
                    <span class="sp-live-line__dot" aria-hidden="true" />
                    Re-sync every {{ spConfig.sync_interval_sec }}s
                    <template v-if="spConfig.live?.last_sync_finished">
                      · Last: {{ formatIso(spConfig.live.last_sync_finished) }}
                      <span v-if="spConfig.live.last_ok === true && spConfig.live.last_total_ingested != null">
                        · {{ spConfig.live.last_total_ingested }} file(s)
                      </span>
                    </template>
                    <span v-if="spConfig.live?.sync_running" class="sp-live-line__run">Sync running…</span>
                    <span v-else-if="spConfig.live?.last_ok === false && spConfig.live?.last_error" class="sp-live-line__err">
                      · {{ spConfig.live.last_error }}
                    </span>
                  </p>
                  <p v-else class="sp-meta sp-meta--small">Live sync is off (<code>SHAREPOINT_LIVE_SYNC=0</code>). Sync manually below.</p>
                  <p class="sp-meta">
                    <span class="sp-meta__k">Site</span> {{ spConfig.site_host || "—" }}
                    <span v-if="spConfig.folder_path" class="sp-meta__path">· {{ spConfig.folder_path }}</span>
                  </p>
                  <p class="sp-meta sp-meta--small">
                    Max {{ spConfig.max_files }} files · depth {{ spConfig.max_depth }} ·
                    {{ Math.round((spConfig.max_bytes_per_file || 0) / (1024 * 1024)) }} MB/file
                  </p>
                  <div class="actions">
                    <button type="button" class="btn" :disabled="spSyncBusy || spConfig?.live?.sync_running" @click="syncSharePoint">
                      {{ spSyncBusy ? "Syncing…" : "Sync now" }}
                    </button>
                  </div>
                  <p v-if="spSyncSummary" class="status">{{ spSyncSummary }}</p>
                </template>
                <p v-else class="status status--muted">Save credentials above to enable sync.</p>
              </template>
            </div>

            <div v-show="activeKbSource === 'gdrive'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect Google Drive</h4>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>Folder ID</span>
                  <input v-model="gdriveForm.folder_id" type="text" placeholder="From the folder URL" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Service account JSON</span>
                  <input ref="gdriveCredFileInput" type="file" accept=".json,application/json" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Or path on API server (optional)</span>
                  <input v-model="gdriveForm.credentials_path" type="text" placeholder="/path/to/sa.json" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="gdriveConnectBusy" @click="connectGoogleDriveStudio">
                  {{ gdriveConnectBusy ? "Saving…" : "Save & connect" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.google_drive?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('google_drive')"
                >
                  Clear saved
                </button>
              </div>
              <p v-if="gdriveConnectStatus" class="status">{{ gdriveConnectStatus }}</p>
              <p class="status status--muted kb-detail__fine">
                Share the folder with the service account email. Uploaded JSON is stored under <code>backend/data/</code>.
              </p>
              <template v-if="gdriveConfig">
                <template v-if="gdriveConfig.configured">
                  <p v-if="gdriveConfig.live_sync_enabled" class="sp-live-line">
                    <span class="sp-live-line__dot" aria-hidden="true" />
                    Re-sync every {{ gdriveConfig.sync_interval_sec }}s
                    <template v-if="gdriveConfig.live?.last_sync_finished">
                      · Last: {{ formatIso(gdriveConfig.live.last_sync_finished) }}
                      <span v-if="gdriveConfig.live.last_ok === true && gdriveConfig.live.last_total_ingested != null">
                        · {{ gdriveConfig.live.last_total_ingested }} file(s)
                      </span>
                    </template>
                    <span v-if="gdriveConfig.live?.sync_running" class="sp-live-line__run">Sync running…</span>
                    <span v-else-if="gdriveConfig.live?.last_ok === false && gdriveConfig.live?.last_error" class="sp-live-line__err">
                      · {{ gdriveConfig.live.last_error }}
                    </span>
                  </p>
                  <p v-else class="sp-meta sp-meta--small">Live sync is off (<code>GOOGLE_DRIVE_LIVE_SYNC=0</code>).</p>
                  <p class="sp-meta">
                    <span class="sp-meta__k">Folder</span> {{ gdriveConfig.folder_id_hint || "—" }}
                  </p>
                  <p class="sp-meta sp-meta--small">
                    Max {{ gdriveConfig.max_files }} files · depth {{ gdriveConfig.max_depth }} ·
                    {{ Math.round((gdriveConfig.max_bytes_per_file || 0) / (1024 * 1024)) }} MB/file
                  </p>
                  <div class="actions">
                    <button
                      type="button"
                      class="btn"
                      :disabled="gdriveSyncBusy || gdriveConfig?.live?.sync_running"
                      @click="syncGoogleDrive"
                    >
                      {{ gdriveSyncBusy ? "Syncing…" : "Sync now" }}
                    </button>
                  </div>
                  <p v-if="gdriveSyncSummary" class="status">{{ gdriveSyncSummary }}</p>
                </template>
                <p v-else class="status status--muted">Provide folder ID and service account JSON (or a server path), then save.</p>
              </template>
            </div>

            <div v-show="activeKbSource === 'dropbox'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect Dropbox</h4>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>Access token (or leave blank if using refresh flow)</span>
                  <input v-model="dbxForm.dropbox_access_token" type="password" autocomplete="off" />
                </label>
                <label class="field">
                  <span>Refresh token</span>
                  <input v-model="dbxForm.dropbox_refresh_token" type="password" autocomplete="off" />
                </label>
                <label class="field">
                  <span>App key</span>
                  <input v-model="dbxForm.dropbox_app_key" type="text" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>App secret</span>
                  <input v-model="dbxForm.dropbox_app_secret" type="password" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Folder path (optional)</span>
                  <input v-model="dbxForm.dropbox_folder_path" type="text" placeholder="/Handbook or leave empty for root" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="dbxConnectBusy" @click="connectDropboxStudio">
                  {{ dbxConnectBusy ? "Saving…" : "Save & connect" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.dropbox?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('dropbox')"
                >
                  Clear saved
                </button>
              </div>
              <p v-if="dbxConnectStatus" class="status">{{ dbxConnectStatus }}</p>
              <p class="status status--muted kb-detail__fine">Scope: <code>files.content.read</code>. Stored locally on this API host.</p>
              <template v-if="dropboxConfig">
                <template v-if="dropboxConfig.configured">
                  <p v-if="dropboxConfig.live_sync_enabled" class="sp-live-line">
                    <span class="sp-live-line__dot" aria-hidden="true" />
                    Re-sync every {{ dropboxConfig.sync_interval_sec }}s
                    <template v-if="dropboxConfig.live?.last_sync_finished">
                      · Last: {{ formatIso(dropboxConfig.live.last_sync_finished) }}
                      <span v-if="dropboxConfig.live.last_ok === true && dropboxConfig.live.last_total_ingested != null">
                        · {{ dropboxConfig.live.last_total_ingested }} file(s)
                      </span>
                    </template>
                    <span v-if="dropboxConfig.live?.sync_running" class="sp-live-line__run">Sync running…</span>
                    <span v-else-if="dropboxConfig.live?.last_ok === false && dropboxConfig.live?.last_error" class="sp-live-line__err">
                      · {{ dropboxConfig.live.last_error }}
                    </span>
                  </p>
                  <p v-else class="sp-meta sp-meta--small">Live sync is off (<code>DROPBOX_LIVE_SYNC=0</code>).</p>
                  <p class="sp-meta">
                    <span class="sp-meta__k">Path</span> {{ dropboxConfig.folder_path_hint || "—" }}
                    <span v-if="dropboxConfig.auth_mode" class="sp-meta__path">· {{ dropboxConfig.auth_mode }}</span>
                  </p>
                  <p class="sp-meta sp-meta--small">
                    Max {{ dropboxConfig.max_files }} files · depth {{ dropboxConfig.max_depth }} ·
                    {{ Math.round((dropboxConfig.max_bytes_per_file || 0) / (1024 * 1024)) }} MB/file
                  </p>
                  <div class="actions">
                    <button
                      type="button"
                      class="btn"
                      :disabled="dropboxSyncBusy || dropboxConfig?.live?.sync_running"
                      @click="syncDropbox"
                    >
                      {{ dropboxSyncBusy ? "Syncing…" : "Sync now" }}
                    </button>
                  </div>
                  <p v-if="dropboxSyncSummary" class="status">{{ dropboxSyncSummary }}</p>
                </template>
                <p v-else class="status status--muted">Enter an access token or refresh token + app credentials, then save.</p>
              </template>
            </div>

            <div v-show="activeKbSource === 's3'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect Amazon S3</h4>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>Bucket name</span>
                  <input v-model="s3Form.s3_bucket" type="text" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Key prefix (optional)</span>
                  <input v-model="s3Form.s3_prefix" type="text" placeholder="folder/subfolder/" />
                </label>
                <label class="field">
                  <span>AWS region</span>
                  <input v-model="s3Form.aws_region" type="text" placeholder="us-east-1" />
                </label>
                <label class="field">
                  <span>Use default credential chain</span>
                  <input v-model="s3Form.s3_use_default_credential_chain" type="text" placeholder="1 or leave empty" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Access key ID</span>
                  <input v-model="s3Form.aws_access_key_id" type="text" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Secret access key</span>
                  <input v-model="s3Form.aws_secret_access_key" type="password" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Session token (optional)</span>
                  <input v-model="s3Form.aws_session_token" type="password" autocomplete="off" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="s3ConnectBusy" @click="connectS3Studio">
                  {{ s3ConnectBusy ? "Saving…" : "Save & connect" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.s3?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('s3')"
                >
                  Clear saved
                </button>
              </div>
              <p v-if="s3ConnectStatus" class="status">{{ s3ConnectStatus }}</p>
              <p class="status status--muted kb-detail__fine">
                Set <code>S3_USE_DEFAULT_CREDENTIAL_CHAIN=1</code> in env or enter keys. Objects are listed under the prefix (recursive virtual folders).
              </p>
              <template v-if="s3Config">
                <template v-if="s3Config.configured">
                  <p v-if="s3Config.live_sync_enabled" class="sp-live-line">
                    <span class="sp-live-line__dot" aria-hidden="true" />
                    Re-sync every {{ s3Config.sync_interval_sec }}s
                    <template v-if="s3Config.live?.last_sync_finished">
                      · Last: {{ formatIso(s3Config.live.last_sync_finished) }}
                      <span v-if="s3Config.live.last_ok === true && s3Config.live.last_total_ingested != null">
                        · {{ s3Config.live.last_total_ingested }} file(s)
                      </span>
                    </template>
                    <span v-if="s3Config.live?.sync_running" class="sp-live-line__run">Sync running…</span>
                    <span v-else-if="s3Config.live?.last_ok === false && s3Config.live?.last_error" class="sp-live-line__err">
                      · {{ s3Config.live.last_error }}
                    </span>
                  </p>
                  <p v-else class="sp-meta sp-meta--small">Live sync is off (<code>S3_LIVE_SYNC=0</code>).</p>
                  <p class="sp-meta">
                    <span class="sp-meta__k">Bucket</span> {{ s3Config.bucket_hint || "—" }}
                    <span v-if="s3Config.auth_mode" class="sp-meta__path">· {{ s3Config.auth_mode }}</span>
                  </p>
                  <p class="sp-meta sp-meta--small">
                    Max {{ s3Config.max_files }} files · depth {{ s3Config.max_depth }} ·
                    {{ Math.round((s3Config.max_bytes_per_file || 0) / (1024 * 1024)) }} MB/file
                  </p>
                  <div class="actions">
                    <button type="button" class="btn" :disabled="s3SyncBusy || s3Config?.live?.sync_running" @click="syncS3">
                      {{ s3SyncBusy ? "Syncing…" : "Sync now" }}
                    </button>
                  </div>
                  <p v-if="s3SyncSummary" class="status">{{ s3SyncSummary }}</p>
                </template>
                <p v-else class="status status--muted">Save bucket and credentials (or default chain), then sync.</p>
              </template>
            </div>

            <div v-show="activeKbSource === 'azure_blob'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect Azure Blob Storage</h4>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>Connection string (or leave blank and use account + key)</span>
                  <input v-model="azureForm.azure_storage_connection_string" type="password" autocomplete="off" />
                </label>
                <label class="field">
                  <span>Storage account name</span>
                  <input v-model="azureForm.azure_storage_account_name" type="text" autocomplete="off" />
                </label>
                <label class="field">
                  <span>Account key</span>
                  <input v-model="azureForm.azure_storage_account_key" type="password" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Container name</span>
                  <input v-model="azureForm.azure_blob_container" type="text" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Blob prefix (optional)</span>
                  <input v-model="azureForm.azure_blob_prefix" type="text" placeholder="handbook/" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="azureConnectBusy" @click="connectAzureBlobStudio">
                  {{ azureConnectBusy ? "Saving…" : "Save & connect" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.azure_blob?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('azure_blob')"
                >
                  Clear saved
                </button>
              </div>
              <p v-if="azureConnectStatus" class="status">{{ azureConnectStatus }}</p>
              <template v-if="azureBlobConfig">
                <template v-if="azureBlobConfig.configured">
                  <p v-if="azureBlobConfig.live_sync_enabled" class="sp-live-line">
                    <span class="sp-live-line__dot" aria-hidden="true" />
                    Re-sync every {{ azureBlobConfig.sync_interval_sec }}s
                    <template v-if="azureBlobConfig.live?.last_sync_finished">
                      · Last: {{ formatIso(azureBlobConfig.live.last_sync_finished) }}
                      <span v-if="azureBlobConfig.live.last_ok === true && azureBlobConfig.live.last_total_ingested != null">
                        · {{ azureBlobConfig.live.last_total_ingested }} file(s)
                      </span>
                    </template>
                    <span v-if="azureBlobConfig.live?.sync_running" class="sp-live-line__run">Sync running…</span>
                    <span v-else-if="azureBlobConfig.live?.last_ok === false && azureBlobConfig.live?.last_error" class="sp-live-line__err">
                      · {{ azureBlobConfig.live.last_error }}
                    </span>
                  </p>
                  <p v-else class="sp-meta sp-meta--small">Live sync is off (<code>AZURE_BLOB_LIVE_SYNC=0</code>).</p>
                  <p class="sp-meta">
                    <span class="sp-meta__k">Container</span> {{ azureBlobConfig.container_hint || "—" }}
                    <span v-if="azureBlobConfig.auth_mode" class="sp-meta__path">· {{ azureBlobConfig.auth_mode }}</span>
                  </p>
                  <p class="sp-meta sp-meta--small">
                    Max {{ azureBlobConfig.max_files }} files · depth {{ azureBlobConfig.max_depth }} ·
                    {{ Math.round((azureBlobConfig.max_bytes_per_file || 0) / (1024 * 1024)) }} MB/file
                  </p>
                  <div class="actions">
                    <button
                      type="button"
                      class="btn"
                      :disabled="azureBlobSyncBusy || azureBlobConfig?.live?.sync_running"
                      @click="syncAzureBlob"
                    >
                      {{ azureBlobSyncBusy ? "Syncing…" : "Sync now" }}
                    </button>
                  </div>
                  <p v-if="azureBlobSyncSummary" class="status">{{ azureBlobSyncSummary }}</p>
                </template>
                <p v-else class="status status--muted">Save connection string or account + key and container.</p>
              </template>
            </div>

            <div v-show="activeKbSource === 'gcs'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect Google Cloud Storage</h4>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>Bucket name</span>
                  <input v-model="gcsForm.bucket" type="text" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Object prefix (optional)</span>
                  <input v-model="gcsForm.prefix" type="text" placeholder="docs/" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Service account JSON</span>
                  <input ref="gcsCredFileInput" type="file" accept=".json,application/json" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Or credentials path on API server</span>
                  <input v-model="gcsForm.credentials_path" type="text" placeholder="/path/to/sa.json" />
                </label>
                <label class="field field--inline studio-form-grid__full">
                  <span>Use application default credentials (GKE / local gcloud)</span>
                  <input v-model="gcsForm.use_adc" type="checkbox" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="gcsConnectBusy" @click="connectGcsStudio">
                  {{ gcsConnectBusy ? "Saving…" : "Save & connect" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.gcs?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('gcs')"
                >
                  Clear saved
                </button>
              </div>
              <p v-if="gcsConnectStatus" class="status">{{ gcsConnectStatus }}</p>
              <template v-if="gcsConfig">
                <template v-if="gcsConfig.configured">
                  <p v-if="gcsConfig.live_sync_enabled" class="sp-live-line">
                    <span class="sp-live-line__dot" aria-hidden="true" />
                    Re-sync every {{ gcsConfig.sync_interval_sec }}s
                    <template v-if="gcsConfig.live?.last_sync_finished">
                      · Last: {{ formatIso(gcsConfig.live.last_sync_finished) }}
                      <span v-if="gcsConfig.live.last_ok === true && gcsConfig.live.last_total_ingested != null">
                        · {{ gcsConfig.live.last_total_ingested }} file(s)
                      </span>
                    </template>
                    <span v-if="gcsConfig.live?.sync_running" class="sp-live-line__run">Sync running…</span>
                    <span v-else-if="gcsConfig.live?.last_ok === false && gcsConfig.live?.last_error" class="sp-live-line__err">
                      · {{ gcsConfig.live.last_error }}
                    </span>
                  </p>
                  <p v-else class="sp-meta sp-meta--small">Live sync is off (<code>GCS_LIVE_SYNC=0</code>).</p>
                  <p class="sp-meta">
                    <span class="sp-meta__k">Bucket</span> {{ gcsConfig.bucket_hint || "—" }}
                    <span v-if="gcsConfig.auth_mode" class="sp-meta__path">· {{ gcsConfig.auth_mode }}</span>
                  </p>
                  <p class="sp-meta sp-meta--small">
                    Max {{ gcsConfig.max_files }} files · depth {{ gcsConfig.max_depth }} ·
                    {{ Math.round((gcsConfig.max_bytes_per_file || 0) / (1024 * 1024)) }} MB/file
                  </p>
                  <div class="actions">
                    <button type="button" class="btn" :disabled="gcsSyncBusy || gcsConfig?.live?.sync_running" @click="syncGcs">
                      {{ gcsSyncBusy ? "Syncing…" : "Sync now" }}
                    </button>
                  </div>
                  <p v-if="gcsSyncSummary" class="status">{{ gcsSyncSummary }}</p>
                </template>
                <p v-else class="status status--muted">Save bucket with JSON key, path, or ADC checkbox.</p>
              </template>
            </div>
          </div>
        </article>

        <article class="flow-card flow-card--vector-hub">
          <div class="flow-card__head flow-card__head--rag">
            <span class="step-badge step-badge--vector">Vec</span>
            <div>
              <h3>Vector database integrations</h3>
              <p class="flow-card__sub">
                Pinecone, Milvus, Weaviate, Qdrant, Elasticsearch, and Azure AI Search credentials for vector search. Values
                are stored in Studio on this host; ChromaDB above remains the active RAG index until a vector pipeline is
                wired in.
              </p>
            </div>
          </div>

          <div class="vector-db-grid" role="group" aria-label="Vector database connectors">
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeVectorDbSource === 'pinecone' }"
              @click="toggleVectorDbSource('pinecone')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--pinecone">Pinecone</span>
                <span class="kb-tile__title">Serverless / pod index</span>
                <span class="kb-tile__meta">{{ studioSummary?.sections?.pinecone?.has_saved ? "Saved" : "Not saved" }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--pinecone" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 3 4 7v10l8 4 8-4V7l-8-4Z" />
                  <path d="M4 7l8 4 8-4M12 11v10" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.pinecone?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeVectorDbSource === 'milvus' }"
              @click="toggleVectorDbSource('milvus')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--milvus">Milvus</span>
                <span class="kb-tile__title">Self-hosted / Zilliz</span>
                <span class="kb-tile__meta">{{ studioSummary?.sections?.milvus?.has_saved ? "Saved" : "Not saved" }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--milvus" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <ellipse cx="12" cy="6" rx="8" ry="3" />
                  <path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
                  <path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.milvus?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeVectorDbSource === 'weaviate' }"
              @click="toggleVectorDbSource('weaviate')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--weaviate">Weaviate</span>
                <span class="kb-tile__title">Graph + vectors</span>
                <span class="kb-tile__meta">{{ studioSummary?.sections?.weaviate?.has_saved ? "Saved" : "Not saved" }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--weaviate" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 3v18M5 8l7-5 7 5M5 16l7 5 7-5" />
                  <circle cx="12" cy="12" r="2.5" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.weaviate?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeVectorDbSource === 'qdrant' }"
              @click="toggleVectorDbSource('qdrant')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--qdrant">Qdrant</span>
                <span class="kb-tile__title">Filterable vectors</span>
                <span class="kb-tile__meta">{{ studioSummary?.sections?.qdrant?.has_saved ? "Saved" : "Not saved" }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--qdrant" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 2 4 6v12l8 4 8-4V6l-8-4Z" />
                  <path d="M12 22V10M4 6l8 4 8-4" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.qdrant?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeVectorDbSource === 'elasticsearch' }"
              @click="toggleVectorDbSource('elasticsearch')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--elastic">Elasticsearch</span>
                <span class="kb-tile__title">dense_vector + BM25</span>
                <span class="kb-tile__meta">{{ studioSummary?.sections?.elasticsearch?.has_saved ? "Saved" : "Not saved" }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--elastic" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M4 15s2-3 8-3 8 3 8 3" />
                  <path d="M4 9s2 3 8 3 8-3 8-3" />
                  <circle cx="12" cy="12" r="2" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.elasticsearch?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeVectorDbSource === 'azure_ai_search' }"
              @click="toggleVectorDbSource('azure_ai_search')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--azure-search">Azure AI Search</span>
                <span class="kb-tile__title">Cognitive Search</span>
                <span class="kb-tile__meta">{{ studioSummary?.sections?.azure_ai_search?.has_saved ? "Saved" : "Not saved" }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--azure-search" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="11" cy="11" r="7" />
                  <path d="M21 21l-4.3-4.3" />
                </svg>
              </span>
              <span v-if="studioSummary?.sections?.azure_ai_search?.has_saved" class="kb-tile__saved">Saved</span>
            </button>
          </div>

          <p v-if="!activeVectorDbSource" class="kb-hint status status--muted">
            Select a vector database above to save connection settings.
          </p>

          <div v-else class="kb-detail">
            <div v-show="activeVectorDbSource === 'pinecone'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect Pinecone</h4>
              <p class="status status--muted kb-detail__fine">
                Use the index name from the Pinecone console. Optional <strong>Host</strong> is the gRPC/HTTPS index endpoint
                (serverless regions). Leave API key blank to keep an existing saved key.
              </p>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>API key</span>
                  <input
                    v-model="pineconeForm.pinecone_api_key"
                    type="password"
                    autocomplete="off"
                    placeholder="pcsk_… or leave blank if already saved"
                  />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Index name</span>
                  <input v-model="pineconeForm.pinecone_index_name" type="text" autocomplete="off" placeholder="my-index" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Index host (optional)</span>
                  <input
                    v-model="pineconeForm.pinecone_host"
                    type="text"
                    autocomplete="off"
                    placeholder="https://…-svc.pinecone.io"
                  />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="pineconeConnectBusy" @click="connectPineconeStudio">
                  {{ pineconeConnectBusy ? "Saving…" : "Save Pinecone" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.pinecone?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('pinecone')"
                >
                  Clear saved Pinecone
                </button>
              </div>
              <p v-if="pineconeConnectStatus" class="status">{{ pineconeConnectStatus }}</p>
            </div>

            <div v-show="activeVectorDbSource === 'milvus'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect Milvus</h4>
              <p class="status status--muted kb-detail__fine">
                <strong>URI</strong> is your Milvus or Zilliz Cloud endpoint (e.g. <code>https://…api…zillizcloud.com:443</code>).
                Token is required for Zilliz Cloud; optional for local Milvus. Leave token blank to keep a saved token.
              </p>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>URI</span>
                  <input
                    v-model="milvusForm.milvus_uri"
                    type="text"
                    autocomplete="off"
                    placeholder="http://127.0.0.1:19530 or Zilliz HTTPS endpoint"
                  />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Token (optional)</span>
                  <input
                    v-model="milvusForm.milvus_token"
                    type="password"
                    autocomplete="off"
                    placeholder="Zilliz API key or leave blank if already saved"
                  />
                </label>
                <label class="field">
                  <span>Database name (optional)</span>
                  <input v-model="milvusForm.milvus_db_name" type="text" autocomplete="off" placeholder="default" />
                </label>
                <label class="field">
                  <span>Collection name</span>
                  <input v-model="milvusForm.milvus_collection_name" type="text" autocomplete="off" placeholder="kb_chunks" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="milvusConnectBusy" @click="connectMilvusStudio">
                  {{ milvusConnectBusy ? "Saving…" : "Save Milvus" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.milvus?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('milvus')"
                >
                  Clear saved Milvus
                </button>
              </div>
              <p v-if="milvusConnectStatus" class="status">{{ milvusConnectStatus }}</p>
            </div>

            <div v-show="activeVectorDbSource === 'weaviate'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect Weaviate</h4>
              <p class="status status--muted kb-detail__fine">
                <strong>URL</strong> is the REST/GraphQL endpoint (e.g. <code>http://localhost:8080</code> or your WCS cluster URL).
                <strong>Class name</strong> is the target collection/schema class. Leave API key blank to keep a saved key.
              </p>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>URL</span>
                  <input v-model="weaviateForm.weaviate_url" type="text" autocomplete="off" placeholder="https://your-cluster.weaviate.network" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>API key (optional)</span>
                  <input
                    v-model="weaviateForm.weaviate_api_key"
                    type="password"
                    autocomplete="off"
                    placeholder="Anonymous local: leave empty"
                  />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Class name</span>
                  <input v-model="weaviateForm.weaviate_class_name" type="text" autocomplete="off" placeholder="Document" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="weaviateConnectBusy" @click="connectWeaviateStudio">
                  {{ weaviateConnectBusy ? "Saving…" : "Save Weaviate" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.weaviate?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('weaviate')"
                >
                  Clear saved Weaviate
                </button>
              </div>
              <p v-if="weaviateConnectStatus" class="status">{{ weaviateConnectStatus }}</p>
            </div>

            <div v-show="activeVectorDbSource === 'qdrant'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect Qdrant</h4>
              <p class="status status--muted kb-detail__fine">
                <strong>URL</strong> is the HTTP API base (e.g. <code>http://localhost:6333</code> or Qdrant Cloud HTTPS URL).
                API key is required for Qdrant Cloud; optional for local. Leave key blank to keep a saved key.
              </p>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>URL</span>
                  <input v-model="qdrantForm.qdrant_url" type="text" autocomplete="off" placeholder="http://127.0.0.1:6333" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>API key (optional)</span>
                  <input v-model="qdrantForm.qdrant_api_key" type="password" autocomplete="off" placeholder="Cloud key or leave blank if saved" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Collection name</span>
                  <input v-model="qdrantForm.qdrant_collection_name" type="text" autocomplete="off" placeholder="kb_chunks" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="qdrantConnectBusy" @click="connectQdrantStudio">
                  {{ qdrantConnectBusy ? "Saving…" : "Save Qdrant" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.qdrant?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('qdrant')"
                >
                  Clear saved Qdrant
                </button>
              </div>
              <p v-if="qdrantConnectStatus" class="status">{{ qdrantConnectStatus }}</p>
            </div>

            <div v-show="activeVectorDbSource === 'elasticsearch'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect Elasticsearch</h4>
              <p class="status status--muted kb-detail__fine">
                <strong>URL</strong> is the cluster REST root (e.g. <code>https://localhost:9200</code> or Elastic Cloud endpoint).
                <strong>API key</strong> can be the Base64 <code>id:api_key</code> form (Elastic Cloud) or leave blank for local/no auth.
                <strong>Index name</strong> should hold your <code>dense_vector</code> mapping when you wire ingest.
              </p>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>URL</span>
                  <input v-model="elasticsearchForm.elasticsearch_url" type="text" autocomplete="off" placeholder="https://my-deployment.es.region.cloud.es.io:443" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>API key (optional)</span>
                  <input
                    v-model="elasticsearchForm.elasticsearch_api_key"
                    type="password"
                    autocomplete="off"
                    placeholder="Elastic API key or leave blank if saved"
                  />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Index name</span>
                  <input v-model="elasticsearchForm.elasticsearch_index_name" type="text" autocomplete="off" placeholder="kb_vectors" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="elasticsearchConnectBusy" @click="connectElasticsearchStudio">
                  {{ elasticsearchConnectBusy ? "Saving…" : "Save Elasticsearch" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.elasticsearch?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('elasticsearch')"
                >
                  Clear saved Elasticsearch
                </button>
              </div>
              <p v-if="elasticsearchConnectStatus" class="status">{{ elasticsearchConnectStatus }}</p>
            </div>

            <div v-show="activeVectorDbSource === 'azure_ai_search'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Connect Azure AI Search</h4>
              <p class="status status--muted kb-detail__fine">
                <strong>Endpoint</strong> is your search service URL (e.g. <code>https://my-search.search.windows.net</code>).
                Use an <strong>admin key</strong> or <strong>query key</strong> from Keys in the Azure portal. Leave the key
                blank to keep an existing saved key when you only change endpoint or index.
              </p>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>Endpoint</span>
                  <input
                    v-model="azureAiSearchForm.azure_ai_search_endpoint"
                    type="text"
                    autocomplete="off"
                    placeholder="https://your-service.search.windows.net"
                  />
                </label>
                <label class="field studio-form-grid__full">
                  <span>API key</span>
                  <input
                    v-model="azureAiSearchForm.azure_ai_search_api_key"
                    type="password"
                    autocomplete="off"
                    placeholder="Admin or query key — leave blank if already saved"
                  />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Index name</span>
                  <input
                    v-model="azureAiSearchForm.azure_ai_search_index_name"
                    type="text"
                    autocomplete="off"
                    placeholder="kb-index"
                  />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="azureAiSearchConnectBusy" @click="connectAzureAiSearchStudio">
                  {{ azureAiSearchConnectBusy ? "Saving…" : "Save Azure AI Search" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.azure_ai_search?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('azure_ai_search')"
                >
                  Clear saved Azure AI Search
                </button>
              </div>
              <p v-if="azureAiSearchConnectStatus" class="status">{{ azureAiSearchConnectStatus }}</p>
            </div>
          </div>
        </article>

        <article class="flow-card flow-card--llm-hub">
          <div class="flow-card__head flow-card__head--rag">
            <span class="step-badge step-badge--rag">LLM</span>
            <div>
              <h3>LLM provider</h3>
              <p class="flow-card__sub">
                Choose where chat completions run for voice turns, RAG answers, and lip-sync Q&amp;A. Cloud keys stay on the
                server; paste a new key only when rotating.
              </p>
            </div>
          </div>

          <div class="llm-grid" role="group" aria-label="LLM providers">
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeLlmSource === 'llm_local' }"
              @click="toggleLlmSource('llm_local')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--local">Local</span>
                <span class="kb-tile__title">Ollama / OpenAI-compatible</span>
                <span class="kb-tile__meta">{{ llmConfig?.provider === "local" ? "Active" : "Inactive" }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--local" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="4" y="4" width="16" height="16" rx="2" />
                  <path d="M9 9h6M9 13h6M9 17h4" />
                </svg>
              </span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeLlmSource === 'llm_openai' }"
              @click="toggleLlmSource('llm_openai')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--openai">OpenAI</span>
                <span class="kb-tile__title">API</span>
                <span class="kb-tile__meta">{{
                  llmConfig?.provider === "openai" ? "Active" : llmConfig?.openai?.configured ? "Key on file" : "Not configured"
                }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--openai" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z" />
                  <path d="M6 18a6 6 0 0 1 12 0" />
                </svg>
              </span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeLlmSource === 'llm_anthropic' }"
              @click="toggleLlmSource('llm_anthropic')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--anthropic">Anthropic</span>
                <span class="kb-tile__title">Claude API</span>
                <span class="kb-tile__meta">{{
                  llmConfig?.provider === "anthropic"
                    ? "Active"
                    : llmConfig?.anthropic?.configured
                      ? "Key on file"
                      : "Not configured"
                }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--anthropic" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 3 4 9v12h16V9l-8-6Z" />
                  <path d="M8 14h8M8 18h5" />
                </svg>
              </span>
            </button>
            <button
              type="button"
              class="kb-tile"
              :class="{ 'kb-tile--active': activeLlmSource === 'llm_google' }"
              @click="toggleLlmSource('llm_google')"
            >
              <div class="kb-tile__main">
                <span class="kb-tile__brand kb-tile__brand--google">Google</span>
                <span class="kb-tile__title">Gemini API</span>
                <span class="kb-tile__meta">{{
                  llmConfig?.provider === "google"
                    ? "Active"
                    : llmConfig?.google?.configured
                      ? "Key on file"
                      : "Not configured"
                }}</span>
              </div>
              <span class="kb-tile__icon kb-tile__icon--google" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z" />
                  <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
                  <path d="M12 3v3M12 18v3M3 12h3M18 12h3" />
                </svg>
              </span>
            </button>
          </div>
          <p v-if="studioSummary?.sections?.llm?.has_saved" class="status status--muted kb-hint">Studio has saved LLM overrides (shown in health / config).</p>

          <p v-if="!activeLlmSource" class="kb-hint status status--muted">Select a provider to view settings.</p>

          <div v-else class="kb-detail">
            <div v-show="activeLlmSource === 'llm_local'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Local server</h4>
              <p class="status status--muted kb-detail__fine">
                Same as <code>OLLAMA_BASE</code> + <code>OLLAMA_MODEL</code>. Use <code>MODEL_API_STYLE=openai</code> for
                <code>/v1/chat/completions</code> gateways.
              </p>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>Ollama / API base URL</span>
                  <input v-model="llmForm.ollama_base" type="text" placeholder="http://127.0.0.1:11434" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Model name</span>
                  <input v-model="llmForm.ollama_model" type="text" placeholder="llama3.2" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>API style</span>
                  <select v-model="llmForm.model_api_style">
                    <option value="auto">auto</option>
                    <option value="openai">openai (chat completions)</option>
                    <option value="ollama">ollama (native generate)</option>
                  </select>
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="llmSaveBusy" @click="connectLlmStudio('local')">
                  {{ llmSaveBusy ? "Saving…" : "Save & use local" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.llm?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('llm')"
                >
                  Clear saved LLM
                </button>
              </div>
              <p v-if="llmSaveStatus" class="status">{{ llmSaveStatus }}</p>
              <template v-if="llmConfig">
                <p class="sp-meta sp-meta--small">
                  Active provider: <strong>{{ llmConfig.provider }}</strong>
                  <span v-if="llmConfig.local?.openai_compatible" class="sp-meta__path">· OpenAI-compatible path</span>
                </p>
              </template>
            </div>

            <div v-show="activeLlmSource === 'llm_openai'" class="kb-detail__panel">
              <h4 class="kb-detail__h">OpenAI</h4>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>API key</span>
                  <input v-model="llmForm.openai_api_key" type="password" autocomplete="off" placeholder="sk-…" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Base URL (optional)</span>
                  <input v-model="llmForm.openai_base_url" type="text" placeholder="https://api.openai.com" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Model</span>
                  <input v-model="llmForm.openai_model" type="text" placeholder="gpt-4o-mini" autocomplete="off" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="llmSaveBusy" @click="connectLlmStudio('openai')">
                  {{ llmSaveBusy ? "Saving…" : "Save & use OpenAI" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.llm?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('llm')"
                >
                  Clear saved LLM
                </button>
              </div>
              <p v-if="llmSaveStatus" class="status">{{ llmSaveStatus }}</p>
              <p v-if="llmConfig?.openai?.configured" class="status status--muted kb-detail__fine">An API key is already stored; leave the field blank to keep it.</p>
            </div>

            <div v-show="activeLlmSource === 'llm_anthropic'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Anthropic</h4>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>API key</span>
                  <input v-model="llmForm.anthropic_api_key" type="password" autocomplete="off" placeholder="sk-ant-…" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Base URL (optional)</span>
                  <input v-model="llmForm.anthropic_base_url" type="text" placeholder="https://api.anthropic.com" autocomplete="off" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Model</span>
                  <input v-model="llmForm.anthropic_model" type="text" placeholder="claude-3-5-haiku-20241022" autocomplete="off" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="llmSaveBusy" @click="connectLlmStudio('anthropic')">
                  {{ llmSaveBusy ? "Saving…" : "Save & use Anthropic" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.llm?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('llm')"
                >
                  Clear saved LLM
                </button>
              </div>
              <p v-if="llmSaveStatus" class="status">{{ llmSaveStatus }}</p>
              <p v-if="llmConfig?.anthropic?.configured" class="status status--muted kb-detail__fine">An API key is already stored; leave the field blank to keep it.</p>
            </div>

            <div v-show="activeLlmSource === 'llm_google'" class="kb-detail__panel">
              <h4 class="kb-detail__h">Google Gemini</h4>
              <p class="status status--muted kb-detail__fine">
                Uses the Generative Language API (<code>streamGenerateContent</code> + <code>alt=sse</code>). Create a key in
                Google AI Studio.
              </p>
              <div class="studio-form-grid">
                <label class="field studio-form-grid__full">
                  <span>API key</span>
                  <input v-model="llmForm.google_api_key" type="password" autocomplete="off" placeholder="GOOGLE_API_KEY" />
                </label>
                <label class="field studio-form-grid__full">
                  <span>API base (optional)</span>
                  <input
                    v-model="llmForm.google_gemini_base_url"
                    type="text"
                    placeholder="https://generativelanguage.googleapis.com"
                    autocomplete="off"
                  />
                </label>
                <label class="field studio-form-grid__full">
                  <span>Model id</span>
                  <input v-model="llmForm.google_gemini_model" type="text" placeholder="gemini-2.0-flash" autocomplete="off" />
                </label>
              </div>
              <div class="actions">
                <button type="button" class="btn" :disabled="llmSaveBusy" @click="connectLlmStudio('google')">
                  {{ llmSaveBusy ? "Saving…" : "Save & use Google" }}
                </button>
                <button
                  v-if="studioSummary?.sections?.llm?.has_saved"
                  type="button"
                  class="btn btn--secondary"
                  @click="clearStudioIntegration('llm')"
                >
                  Clear saved LLM
                </button>
              </div>
              <p v-if="llmSaveStatus" class="status">{{ llmSaveStatus }}</p>
              <p v-if="llmConfig?.google?.configured" class="status status--muted kb-detail__fine">
                An API key is already stored; leave the field blank to keep it. You can also set <code>GEMINI_API_KEY</code> in
                <code>.env</code> instead of <code>GOOGLE_API_KEY</code>.
              </p>
            </div>
          </div>
        </article>

      </div>
    </section>
  </div>
</template>

<style scoped>
.studio-page {
  --ink: #0b1220;
  --muted: #64748b;
  --line: rgba(148, 163, 184, 0.35);
  --card: rgba(255, 255, 255, 0.72);
  --card-solid: #f8fafc;
  --accent: #14b8a6;
  --accent-2: #7c3aed;
  --warn: #f59e0b;
  min-height: 100vh;
  position: relative;
  color: var(--ink);
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}

.studio-page__glow {
  pointer-events: none;
  position: fixed;
  inset: 0;
  background:
    radial-gradient(1200px 600px at 12% -10%, rgba(20, 184, 166, 0.22), transparent 55%),
    radial-gradient(900px 500px at 88% 0%, rgba(124, 58, 237, 0.18), transparent 50%),
    radial-gradient(800px 480px at 50% 100%, rgba(59, 130, 246, 0.12), transparent 55%),
    linear-gradient(180deg, #e8eef6 0%, #e4e2e2 38%, #eef2f7 100%);
  z-index: 0;
}

.studio {
  position: relative;
  z-index: 1;
  max-width: 1120px;
  margin: 0 auto;
  padding: clamp(1.1rem, 3vw, 2.25rem) clamp(1rem, 3vw, 2rem) 2.5rem;
}

.studio-stack {
  display: flex;
  flex-direction: column;
  gap: clamp(0.85rem, 2vw, 1.25rem);
}

.hero {
  margin-bottom: clamp(1.25rem, 3vw, 2rem);
  padding: clamp(1.1rem, 2.5vw, 1.65rem) clamp(1.1rem, 2.5vw, 1.75rem);
  border-radius: 20px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.9), rgba(248, 250, 252, 0.78));
  border: 1px solid var(--line);
  box-shadow: 0 18px 50px rgba(15, 23, 42, 0.08), 0 1px 0 rgba(255, 255, 255, 0.8) inset;
  backdrop-filter: blur(12px);
}

.hero__nav {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.85rem;
}

.hero__title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.65rem 1rem;
  margin-bottom: 0.45rem;
}

.hero h1 {
  margin: 0;
  font-size: clamp(1.55rem, 2.8vw, 2.35rem);
  font-weight: 800;
  letter-spacing: -0.03em;
  background: linear-gradient(120deg, #0f766e, #5b21b6 55%, #0e7490);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.hero__lede {
  margin: 0;
  max-width: 52ch;
  font-size: clamp(0.95rem, 1.35vw, 1.05rem);
  line-height: 1.55;
  color: var(--muted);
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.38rem 0.85rem;
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 600;
  text-decoration: none;
  border: 1px solid var(--line);
  transition: background 0.15s ease, border-color 0.15s ease, transform 0.12s ease;
}

.pill--ghost {
  background: rgba(255, 255, 255, 0.55);
  color: #334155;
}

.pill--ghost:hover {
  background: #fff;
  border-color: rgba(20, 184, 166, 0.45);
  transform: translateY(-1px);
}

.pill--link {
  background: rgba(15, 118, 110, 0.1);
  color: #0f766e;
  border-color: rgba(20, 184, 166, 0.35);
}

.pill--link:hover {
  background: rgba(15, 118, 110, 0.16);
}

.chip {
  font-size: 0.78rem;
  font-weight: 700;
  padding: 0.28rem 0.65rem;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.06);
  color: #475569;
  border: 1px solid rgba(148, 163, 184, 0.35);
}

.chip--ok {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
  border-color: rgba(16, 185, 129, 0.35);
}

.voice-hub-panels {
  display: grid;
  gap: clamp(0.85rem, 2vw, 1.25rem);
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--line);
}

.voice-hub-panels__hint {
  margin: 0;
  padding: 0.65rem 0.35rem 0.15rem;
  font-size: 0.88rem;
  color: var(--muted);
  text-align: center;
  line-height: 1.45;
}

.flow-card--voice-hub .voice-hub-panels .flow-card {
  background: var(--card-solid);
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.05);
}

.flow-card {
  position: relative;
  border-radius: 18px;
  padding: clamp(1rem, 2vw, 1.35rem);
  background: var(--card);
  border: 1px solid var(--line);
  box-shadow: 0 14px 36px rgba(15, 23, 42, 0.06);
  backdrop-filter: blur(10px);
  overflow: hidden;
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
}

.flow-card::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  background: linear-gradient(180deg, var(--accent), var(--accent-2));
  opacity: 0.85;
}

.flow-card:hover {
  border-color: rgba(20, 184, 166, 0.35);
  box-shadow: 0 20px 44px rgba(15, 23, 42, 0.09);
}

.flow-card--live {
  border-color: rgba(245, 158, 11, 0.55);
  box-shadow: 0 0 0 1px rgba(245, 158, 11, 0.2), 0 18px 40px rgba(245, 158, 11, 0.12);
}

.flow-card__head {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 0.65rem 0.85rem;
  align-items: start;
  margin-bottom: 1rem;
}

.flow-card__head h3 {
  margin: 0;
  font-size: clamp(1.02rem, 1.6vw, 1.2rem);
  font-weight: 800;
  letter-spacing: -0.02em;
  color: #0f172a;
}

.flow-card__sub {
  margin: 0.25rem 0 0;
  font-size: 0.86rem;
  line-height: 1.45;
  color: var(--muted);
}

.flow-card__sub code {
  font-size: 0.8em;
  padding: 0.12rem 0.35rem;
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.06);
  color: #4338ca;
}

.step-badge {
  display: inline-grid;
  place-items: center;
  width: 2.35rem;
  height: 2.35rem;
  border-radius: 12px;
  font-weight: 800;
  font-size: 1rem;
  color: #fff;
  background: linear-gradient(145deg, var(--accent), var(--accent-2));
  box-shadow: 0 6px 16px rgba(124, 58, 237, 0.25);
}

.rec-dot {
  width: 0.85rem;
  height: 0.85rem;
  border-radius: 50%;
  background: #ef4444;
  position: relative;
  margin-top: 0.35rem;
  justify-self: end;
}

.rec-dot__pulse {
  position: absolute;
  inset: -6px;
  border-radius: 50%;
  border: 2px solid rgba(239, 68, 68, 0.45);
  animation: pulse-ring 1.4s ease-out infinite;
}

@keyframes pulse-ring {
  0% {
    transform: scale(0.65);
    opacity: 1;
  }
  100% {
    transform: scale(1.6);
    opacity: 0;
  }
}

.field {
  display: grid;
  gap: 0.35rem;
  margin-bottom: 0.75rem;
}

.field--inline {
  max-width: 16rem;
}

.field > span {
  font-weight: 700;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #475569;
}

textarea,
select,
input[type="text"],
input[type="password"] {
  width: 100%;
  border: 1px solid rgba(148, 163, 184, 0.55);
  border-radius: 12px;
  padding: 0.65rem 0.8rem;
  font: inherit;
  font-size: 0.95rem;
  background: var(--card-solid);
  color: var(--ink);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

textarea {
  min-height: 6.5rem;
  resize: vertical;
  line-height: 1.5;
}

textarea:focus,
select:focus,
input[type="text"]:focus,
input[type="password"]:focus {
  outline: none;
  border-color: rgba(20, 184, 166, 0.65);
  box-shadow: 0 0 0 3px rgba(20, 184, 166, 0.18);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.55rem 0.65rem;
  margin: 0.35rem 0 0.85rem;
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  border: none;
  border-radius: 999px;
  padding: 0.55rem 1.05rem;
  font: inherit;
  font-size: 0.9rem;
  font-weight: 700;
  cursor: pointer;
  color: #fff;
  background: linear-gradient(135deg, #0d9488, #6d28d9);
  box-shadow: 0 8px 22px rgba(109, 40, 217, 0.22);
  transition: transform 0.12s ease, filter 0.12s ease, opacity 0.15s ease;
}

.btn:hover:not(:disabled) {
  filter: brightness(1.05);
  transform: translateY(-1px);
}

.btn:active:not(:disabled) {
  transform: translateY(0);
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  box-shadow: none;
}

.btn--secondary {
  background: rgba(15, 23, 42, 0.08);
  color: #1e293b;
  box-shadow: none;
  border: 1px solid rgba(148, 163, 184, 0.45);
}

.btn--secondary:hover:not(:disabled) {
  background: rgba(15, 23, 42, 0.1);
}

.btn--warn {
  background: linear-gradient(135deg, #ea580c, #dc2626);
  box-shadow: 0 8px 22px rgba(220, 38, 38, 0.22);
}

.btn__icon {
  font-size: 0.65rem;
  opacity: 0.95;
}

.btn__icon--blink {
  animation: blink 1s step-end infinite;
}

@keyframes blink {
  50% {
    opacity: 0.25;
  }
}

.audio-shell {
  margin-top: 0.35rem;
  padding: 0.65rem 0.75rem;
  border-radius: 14px;
  background: rgba(15, 23, 42, 0.04);
  border: 1px solid rgba(148, 163, 184, 0.35);
}

.audio-shell--accent {
  background: linear-gradient(135deg, rgba(20, 184, 166, 0.1), rgba(124, 58, 237, 0.08));
  border-color: rgba(20, 184, 166, 0.35);
}

.audio {
  width: 100%;
  max-width: 100%;
  vertical-align: middle;
}

.status {
  margin: 0.65rem 0 0;
  font-size: 0.88rem;
  line-height: 1.45;
  color: #334155;
}

.status--muted {
  color: #94a3b8;
  font-style: italic;
}

.mini-check {
  list-style: none;
  margin: 0.85rem 0 0;
  padding: 0;
  display: grid;
  gap: 0.4rem;
  font-size: 0.82rem;
  color: var(--muted);
}

.mini-check li {
  display: flex;
  align-items: center;
  gap: 0.45rem;
}

.mini-check li::before {
  content: "";
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  background: #cbd5e1;
  flex-shrink: 0;
}

.mini-check__on::before {
  background: linear-gradient(135deg, #10b981, #14b8a6);
  box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.25);
}

.mini-check__on {
  color: #0f766e;
  font-weight: 600;
}

.flow-card__head--rag {
  grid-template-columns: auto 1fr auto;
}

.step-badge--rag {
  background: linear-gradient(145deg, #0ea5e9, #6366f1);
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.3);
}

.step-badge--video {
  font-size: 0.72rem;
  letter-spacing: 0.02em;
  background: linear-gradient(145deg, #7c3aed, #db2777);
  box-shadow: 0 6px 16px rgba(124, 58, 237, 0.32);
}

.step-badge--voice-hub {
  font-size: 0.78rem;
  background: linear-gradient(145deg, #0d9488, #2563eb);
  box-shadow: 0 6px 16px rgba(13, 148, 136, 0.28);
}

.flow-card--video-hub::before {
  background: linear-gradient(180deg, #a855f7, #ec4899);
}

.flow-card--voice-hub::before {
  background: linear-gradient(180deg, #14b8a6, #3b82f6);
}

.studio-hub-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10.5rem, 1fr));
  gap: 0.65rem;
  margin-bottom: 0.35rem;
}

@media (min-width: 720px) {
  .studio-hub-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.kb-tile--disabled,
.kb-tile--disabled:hover {
  cursor: default;
  opacity: 0.72;
  transform: none;
  pointer-events: none;
  border-color: rgba(148, 163, 184, 0.45);
  box-shadow: none;
}

.kb-tile__soon {
  position: absolute;
  top: 0.45rem;
  right: 0.45rem;
  font-size: 0.62rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0.15rem 0.4rem;
  border-radius: 6px;
  background: rgba(100, 116, 139, 0.2);
  color: #475569;
}

.kb-tile__brand--video {
  color: #9333ea;
}

.kb-tile__icon--video {
  color: #9333ea;
  background: rgba(147, 51, 234, 0.12);
}

.kb-tile__brand--voice-rec {
  color: #0d9488;
}

.kb-tile__icon--voice-rec {
  color: #0d9488;
  background: rgba(13, 148, 136, 0.12);
}

.kb-tile__brand--voice-clone {
  color: #2563eb;
}

.kb-tile__icon--voice-clone {
  color: #2563eb;
  background: rgba(37, 99, 235, 0.12);
}

.kb-tile__brand--voice-gen {
  color: #7c3aed;
}

.kb-tile__icon--voice-gen {
  color: #7c3aed;
  background: rgba(124, 58, 237, 0.12);
}

.rag-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.75rem 1rem;
  margin-bottom: 0.75rem;
}

.rag-file {
  display: grid;
  gap: 0.35rem;
  flex: 1;
  min-width: min(22rem, 100%);
}

.rag-file input[type="file"] {
  font-size: 0.82rem;
}

.rag-file__btn {
  display: inline-flex;
  width: fit-content;
  padding: 0.45rem 0.85rem;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.06);
  border: 1px dashed rgba(148, 163, 184, 0.8);
  font-size: 0.82rem;
  font-weight: 600;
  color: #475569;
}

.rag-file__hint {
  font-size: 0.78rem;
  color: #94a3b8;
}

.rag-path {
  margin: 0.35rem 0 0;
  font-size: 0.78rem;
  color: #94a3b8;
  word-break: break-all;
}

.rag-path__label {
  font-weight: 700;
  color: #64748b;
  margin-right: 0.35rem;
}

.rag-table-wrap {
  margin-top: 0.85rem;
  overflow: auto;
  border-radius: 12px;
  border: 1px solid rgba(148, 163, 184, 0.45);
  background: rgba(255, 255, 255, 0.65);
}

.rag-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.86rem;
}

.rag-table th,
.rag-table td {
  text-align: left;
  padding: 0.55rem 0.75rem;
  border-bottom: 1px solid rgba(226, 232, 240, 0.9);
}

.rag-table th {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #64748b;
  background: rgba(241, 245, 249, 0.9);
}

.rag-table tr:last-child td {
  border-bottom: none;
}

.btn-mini {
  border: none;
  border-radius: 8px;
  padding: 0.28rem 0.55rem;
  font: inherit;
  font-size: 0.78rem;
  font-weight: 700;
  cursor: pointer;
  color: #b91c1c;
  background: rgba(254, 226, 226, 0.85);
}

.btn-mini:hover {
  background: rgba(254, 202, 202, 0.95);
}

.rag-query {
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid rgba(148, 163, 184, 0.35);
}

.rag-hits {
  list-style: none;
  margin: 0.65rem 0 0;
  padding: 0;
  display: grid;
  gap: 0.55rem;
}

.rag-hit {
  margin: 0;
  padding: 0.65rem 0.75rem;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.04);
  border: 1px solid rgba(148, 163, 184, 0.35);
}

.rag-hit__meta {
  display: block;
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #64748b;
  margin-bottom: 0.35rem;
}

.rag-hit__text {
  margin: 0;
  font-size: 0.86rem;
  line-height: 1.45;
  color: #334155;
}

.flow-card--sp {
  border-left: 4px solid #0078d4;
}

.step-badge--sp {
  background: linear-gradient(145deg, #0078d4, #106ebe);
  box-shadow: 0 6px 16px rgba(0, 120, 212, 0.28);
}

.flow-card--gdrive {
  border-left: 4px solid #1a73e8;
}

.step-badge--gdrive {
  background: linear-gradient(145deg, #1a73e8, #0d47a1);
  box-shadow: 0 6px 16px rgba(26, 115, 232, 0.28);
}

.flow-card--dropbox {
  border-left: 4px solid #0061fe;
}

.step-badge--dropbox {
  background: linear-gradient(145deg, #0061fe, #0047b3);
  box-shadow: 0 6px 16px rgba(0, 97, 254, 0.28);
}

.sp-meta {
  margin: 0 0 0.35rem;
  font-size: 0.88rem;
  color: #475569;
  word-break: break-word;
}

.sp-meta--small {
  font-size: 0.8rem;
  color: #94a3b8;
  margin-bottom: 0.65rem;
}

.sp-meta__k {
  font-weight: 700;
  margin-right: 0.35rem;
  color: #64748b;
}

.sp-meta__path {
  color: #64748b;
}

.flow-card code {
  font-size: 0.78em;
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.06);
}

.chip--live {
  background: rgba(16, 185, 129, 0.18);
  color: #047857;
  border-color: rgba(16, 185, 129, 0.4);
  animation: chip-live-pulse 2s ease-in-out infinite;
}

@keyframes chip-live-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.35);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(16, 185, 129, 0);
  }
}

.sp-live-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem 0.35rem;
  margin: 0 0 0.65rem;
  font-size: 0.86rem;
  color: #334155;
  line-height: 1.45;
}

.sp-live-line__dot {
  display: inline-block;
  width: 0.45rem;
  height: 0.45rem;
  border-radius: 50%;
  background: #10b981;
  margin-right: 0.15rem;
  box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.35);
}

.sp-live-line__run {
  color: #0d9488;
  font-weight: 700;
}

.sp-live-line__err {
  color: #b91c1c;
  font-size: 0.82rem;
}

.flow-card--kb-hub::before {
  background: linear-gradient(180deg, #0ea5e9, #6366f1);
}

.flow-card--vector-hub::before {
  background: linear-gradient(180deg, #6366f1, #14b8a6);
}

.step-badge--vector {
  font-size: 0.72rem;
  letter-spacing: 0.04em;
  background: linear-gradient(145deg, #4f46e5, #0d9488);
  box-shadow: 0 6px 16px rgba(79, 70, 229, 0.28);
}

.vector-db-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.65rem;
  margin-bottom: 0.35rem;
}

.kb-tile__brand--pinecone {
  color: #4338ca;
}

.kb-tile__icon--pinecone {
  color: #4338ca;
  background: rgba(67, 56, 202, 0.12);
}

.kb-tile__brand--milvus {
  color: #0f766e;
}

.kb-tile__icon--milvus {
  color: #0f766e;
  background: rgba(15, 118, 110, 0.12);
}

.kb-tile__brand--weaviate {
  color: #5b21b6;
}

.kb-tile__icon--weaviate {
  color: #5b21b6;
  background: rgba(91, 33, 182, 0.12);
}

.kb-tile__brand--qdrant {
  color: #b91c1c;
}

.kb-tile__icon--qdrant {
  color: #b91c1c;
  background: rgba(185, 28, 28, 0.1);
}

.kb-tile__brand--elastic {
  color: #b45309;
}

.kb-tile__icon--elastic {
  color: #b45309;
  background: rgba(180, 83, 9, 0.12);
}

.kb-tile__brand--azure-search {
  color: #0078d4;
}

.kb-tile__icon--azure-search {
  color: #0078d4;
  background: rgba(0, 120, 212, 0.12);
}

.kb-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.65rem;
  margin-bottom: 0.35rem;
}

@media (min-width: 900px) {
  .kb-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

.llm-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10.5rem, 1fr));
  gap: 0.65rem;
  margin-bottom: 0.35rem;
}

@media (min-width: 720px) {
  .llm-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (min-width: 960px) {
  .llm-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

.kb-tile {
  position: relative;
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 0.55rem;
  padding: 0.75rem 0.85rem;
  min-height: 6.25rem;
  text-align: left;
  border-radius: 14px;
  border: 2px solid rgba(148, 163, 184, 0.45);
  background: rgba(255, 255, 255, 0.75);
  cursor: pointer;
  font: inherit;
  color: #0f172a;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.12s ease;
}

.kb-tile:hover {
  border-color: rgba(20, 184, 166, 0.45);
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.08);
}

.kb-tile--active {
  border-color: rgba(20, 184, 166, 0.85);
  box-shadow: 0 0 0 3px rgba(20, 184, 166, 0.2);
  transform: translateY(-1px);
}

.kb-tile__main {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.2rem;
  min-width: 0;
  flex: 1;
  text-align: left;
}

.kb-tile__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.4rem;
  height: 2.4rem;
  border-radius: 11px;
  flex-shrink: 0;
  align-self: center;
}

.kb-tile__icon svg {
  width: 1.22rem;
  height: 1.22rem;
}

.kb-tile__icon--chroma {
  color: #4f46e5;
  background: rgba(79, 70, 229, 0.12);
}

.kb-tile__icon--sp {
  color: #0078d4;
  background: rgba(0, 120, 212, 0.12);
}

.kb-tile__icon--gd {
  color: #1a73e8;
  background: rgba(26, 115, 232, 0.12);
}

.kb-tile__icon--db {
  color: #0061fe;
  background: rgba(0, 97, 254, 0.12);
}

.kb-tile__icon--s3 {
  color: #e47911;
  background: rgba(228, 121, 17, 0.14);
}

.kb-tile__icon--azblob {
  color: #0078d4;
  background: rgba(0, 120, 212, 0.12);
}

.kb-tile__icon--gcs {
  color: #4285f4;
  background: rgba(66, 133, 244, 0.12);
}

.kb-tile__icon--local {
  color: #0d9488;
  background: rgba(13, 148, 136, 0.12);
}

.kb-tile__icon--openai {
  color: #10a37f;
  background: rgba(16, 163, 127, 0.12);
}

.kb-tile__icon--anthropic {
  color: #c45c26;
  background: rgba(196, 92, 38, 0.12);
}

.kb-tile__icon--google {
  color: #4285f4;
  background: rgba(66, 133, 244, 0.12);
}

.kb-tile__brand {
  font-size: 0.68rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #64748b;
}

.kb-tile__brand--chroma {
  color: #4f46e5;
}

.kb-tile__brand--sp {
  color: #0078d4;
}

.kb-tile__brand--gd {
  color: #1a73e8;
}

.kb-tile__brand--db {
  color: #0061fe;
}

.kb-tile__brand--s3 {
  color: #e47911;
}

.kb-tile__brand--azblob {
  color: #0078d4;
}

.kb-tile__brand--gcs {
  color: #4285f4;
}

.kb-tile__brand--local {
  color: #0d9488;
}

.kb-tile__brand--openai {
  color: #10a37f;
}

.kb-tile__brand--anthropic {
  color: #c45c26;
}

.kb-tile__brand--google {
  color: #4285f4;
}

.kb-tile__title {
  font-size: 0.95rem;
  font-weight: 800;
  letter-spacing: -0.02em;
}

.kb-tile__meta {
  font-size: 0.78rem;
  color: #64748b;
  line-height: 1.35;
}

.kb-tile__saved {
  position: absolute;
  top: 0.45rem;
  right: 3rem;
  font-size: 0.62rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0.15rem 0.4rem;
  border-radius: 6px;
  background: rgba(16, 185, 129, 0.2);
  color: #047857;
}

.kb-hint {
  margin: 0.35rem 0 0;
}

.kb-detail {
  margin-top: 0.65rem;
  padding: 1rem 1.05rem 0.85rem;
  border-radius: 16px;
  background: rgba(248, 250, 252, 0.85);
  border: 1px solid rgba(148, 163, 184, 0.4);
}

.kb-detail__panel {
  animation: kb-fade 0.2s ease;
}

@keyframes kb-fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.kb-detail__h {
  margin: 0 0 0.75rem;
  font-size: 1rem;
  font-weight: 800;
  color: #0f172a;
}

.kb-detail__fine {
  font-size: 0.8rem !important;
  margin-top: 0.5rem !important;
}

.studio-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.55rem 0.85rem;
  margin-bottom: 0.35rem;
}

@media (max-width: 640px) {
  .studio-form-grid {
    grid-template-columns: 1fr;
  }
}

.studio-form-grid__full {
  grid-column: 1 / -1;
}
</style>
