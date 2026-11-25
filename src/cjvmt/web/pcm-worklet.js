class CapturePCM extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.buffer = new Int16Array(options.processorOptions.chunk * 8);
    this.position = 0;
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const sample of channel) {
      this.buffer[this.position++] = Math.max(-32768, Math.min(32767, Math.round(sample * 32768)));
      if (this.position === this.buffer.length) {
        this.port.postMessage(this.buffer.buffer, [this.buffer.buffer]);
        this.buffer = new Int16Array(this.position);
        this.position = 0;
      }
    }
    return true;
  }
}
registerProcessor('cjvmt-capture', CapturePCM);
