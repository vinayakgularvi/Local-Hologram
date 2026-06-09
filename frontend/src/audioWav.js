/** Decode browser-recorded audio (WebM/OGG) and encode as 16-bit mono WAV for TTS reference upload. */

function writeString(view, offset, str) {
  for (let i = 0; i < str.length; i += 1) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}

function encodeWavMonoPcm16(samples, sampleRate) {
  const numSamples = samples.length;
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);
  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + numSamples * 2, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, "data");
  view.setUint32(40, numSamples * 2, true);
  let offset = 44;
  for (let i = 0; i < numSamples; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function mixDownToMono(audioBuffer) {
  const len = audioBuffer.length;
  const out = new Float32Array(len);
  const channels = audioBuffer.numberOfChannels;
  for (let ch = 0; ch < channels; ch += 1) {
    const data = audioBuffer.getChannelData(ch);
    for (let i = 0; i < len; i += 1) {
      out[i] += data[i] / channels;
    }
  }
  return out;
}

function isWavFile(file) {
  const name = String(file?.name || "").toLowerCase();
  const type = String(file?.type || "").toLowerCase();
  return name.endsWith(".wav") || type.includes("wav");
}

export async function audioFileToWavFile(file, outName = "reference.wav") {
  if (!file) throw new Error("No audio file.");
  if (isWavFile(file)) {
    if (file.name.toLowerCase().endsWith(".wav")) return file;
    return new File([file], outName, { type: "audio/wav" });
  }
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) throw new Error("Audio decoding is not supported in this browser.");
  const ctx = new AC();
  try {
    const arrayBuffer = await file.arrayBuffer();
    const audioBuffer = await ctx.decodeAudioData(arrayBuffer.slice(0));
    const mono = mixDownToMono(audioBuffer);
    const wavBlob = encodeWavMonoPcm16(mono, audioBuffer.sampleRate);
    return new File([wavBlob], outName, { type: "audio/wav" });
  } finally {
    await ctx.close().catch(() => {});
  }
}
