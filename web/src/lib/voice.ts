/** Gemini Live over the vendor-svc WebSocket: 16 kHz PCM16 up, 24 kHz PCM16 down. */

const WORKLET = `
class Pcm16 extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0][0];
    if (ch) {
      const out = new Int16Array(ch.length);
      for (let i = 0; i < ch.length; i++) out[i] = Math.max(-1, Math.min(1, ch[i])) * 0x7fff;
      this.port.postMessage(out.buffer, [out.buffer]);
    }
    return true;
  }
}
registerProcessor("pcm16", Pcm16);`;

export type LiveMessage =
  | { type: "transcript"; role: "user" | "model"; text: string }
  | { type: "event"; [key: string]: unknown }
  | { type: "error"; error: string; detail?: string };

export class LiveVoice {
  private ws: WebSocket;
  private capture?: AudioContext;
  private stream?: MediaStream;
  private playback = new AudioContext({ sampleRate: 24000 });
  private playAt = 0;
  private sending = false;

  constructor(url: string, language: string, onMessage: (m: LiveMessage) => void) {
    this.ws = new WebSocket(url);
    this.ws.binaryType = "arraybuffer";
    this.ws.onopen = () => this.ws.send(JSON.stringify({ type: "start", language }));
    this.ws.onmessage = (e) => {
      if (typeof e.data === "string") onMessage(JSON.parse(e.data) as LiveMessage);
      else this.play(e.data as ArrayBuffer);
    };
  }

  async press(): Promise<void> {
    if (!this.capture) {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } });
      this.capture = new AudioContext({ sampleRate: 16000 });
      const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
      await this.capture.audioWorklet.addModule(url);
      const node = new AudioWorkletNode(this.capture, "pcm16");
      node.port.onmessage = (e) => this.sending && this.ws.readyState === WebSocket.OPEN && this.ws.send(e.data);
      this.capture.createMediaStreamSource(this.stream).connect(node);
    }
    this.sending = true;
  }

  release(): void {
    this.sending = false;
  }

  private play(buffer: ArrayBuffer): void {
    const pcm = new Int16Array(buffer);
    const audio = this.playback.createBuffer(1, pcm.length, 24000);
    const data = audio.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) data[i] = pcm[i] / 0x8000;
    const src = this.playback.createBufferSource();
    src.buffer = audio;
    src.connect(this.playback.destination);
    this.playAt = Math.max(this.playAt, this.playback.currentTime);
    src.start(this.playAt);
    this.playAt += audio.duration;
  }

  close(): void {
    this.ws.close();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.capture?.close();
    void this.playback.close();
  }
}
