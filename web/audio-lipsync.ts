import { setMouthOpen } from "./avatar";

/**
 * Play MP3 and drive `--mouth-open` from the audio signal.
 * Uses speech-band energy + waveform RMS (fixes wrong buffer size for time-domain data).
 */
export function playMp3WithLipSync(blob: Blob): Promise<void> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const audio = new Audio();
    audio.src = url;

    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    const source = ctx.createMediaElementSource(audio);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.35;
    source.connect(analyser);
    analyser.connect(ctx.destination);

    // getByteTimeDomainData must use length === fftSize (not frequencyBinCount)
    const timeData = new Uint8Array(analyser.fftSize);
    const freqData = new Uint8Array(analyser.frequencyBinCount);

    let anim = 0;
    let smooth = 0;
    let noiseFloor = 0;

    const step = (): void => {
      analyser.getByteTimeDomainData(timeData);
      analyser.getByteFrequencyData(freqData);

      let sum = 0;
      for (let i = 0; i < timeData.length; i += 1) {
        const n = (timeData[i]! - 128) / 128;
        sum += n * n;
      }
      const rms = Math.sqrt(sum / timeData.length);

      const nyquist = ctx.sampleRate / 2;
      const nBins = analyser.frequencyBinCount;
      const i0 = Math.max(0, Math.floor((300 / nyquist) * nBins));
      const i1 = Math.min(nBins - 1, Math.ceil((4000 / nyquist) * nBins));
      let bandSum = 0;
      for (let i = i0; i <= i1; i += 1) {
        bandSum += freqData[i]!;
      }
      const band = bandSum / (i1 - i0 + 1) / 255;

      noiseFloor = Math.min(noiseFloor * 0.995 + rms * 0.005, 0.08);
      const gatedRms = Math.max(0, rms - noiseFloor * 1.5);

      const raw = Math.min(
        1,
        gatedRms * 7.2 * 0.42 + band * 2.4 * 0.58,
      );

      const attack = 0.55;
      const release = 0.78;
      if (raw > smooth) {
        smooth = smooth * (1 - attack) + raw * attack;
      } else {
        smooth = smooth * release + raw * (1 - release);
      }

      const shaped = Math.pow(Math.max(0, Math.min(1, smooth)), 0.72);
      setMouthOpen(shaped);

      anim = requestAnimationFrame(step);
    };

    const cleanup = (): void => {
      if (anim) cancelAnimationFrame(anim);
      setMouthOpen(0);
      void ctx.close();
      URL.revokeObjectURL(url);
    };

    audio.onended = () => {
      cleanup();
      resolve();
    };

    audio.onerror = () => {
      cleanup();
      reject(new Error("Audio playback failed"));
    };

    void ctx
      .resume()
      .then(() => audio.play())
      .then(() => {
        anim = requestAnimationFrame(step);
      })
      .catch((e: unknown) => {
        cleanup();
        reject(e instanceof Error ? e : new Error(String(e)));
      });
  });
}
