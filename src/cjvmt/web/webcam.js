'use strict';
class WebcamCapture {
  static async open(settings, video) {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      throw Error('摄像头需通过 HTTPS 或 localhost 打开。');
    }
    const audio = new AudioContext({sampleRate: settings.rate});
    let stream;
    try {
      if (audio.sampleRate !== settings.rate) throw Error('此浏览器无法提供所需的音频采样率。');
      await audio.resume();
      stream = await navigator.mediaDevices.getUserMedia({
        video: {width: {ideal: 720}, frameRate: {ideal: 30, max: 30}},
        audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false},
      });
      await audio.audioWorklet.addModule('/assets/pcm-worklet.js');
      video.srcObject = stream;
      video.muted = true;
      await video.play();
      return new WebcamCapture(settings, video, audio, stream);
    } catch (error) {
      stream?.getTracks().forEach(track => track.stop());
      await audio.close();
      video.srcObject = null;
      video.muted = false;
      throw error;
    }
  }
  constructor(settings, video, audio, stream) {
    this.settings = settings;
    this.video = video;
    this.audio = audio;
    this.stream = stream;
    this.canvas = document.createElement('canvas');
    this.pending = Promise.resolve();
    this.queued = 0;
    this.sent = 0;
    this.nextFrame = 0;
    this.closed = false;
    if (typeof MediaRecorder !== 'undefined') {
      const mimeType = ['video/webm;codecs=vp8,opus', 'video/mp4', 'video/webm'].find(type => MediaRecorder.isTypeSupported(type));
      if (mimeType) this.recorder = new MediaRecorder(stream, {mimeType, videoBitsPerSecond: 1_250_000});
    }
  }
  start(job, onFailure, onLimit) {
    this.job = job;
    this.source = this.audio.createMediaStreamSource(this.stream);
    this.processor = new AudioWorkletNode(this.audio, 'cjvmt-capture', {
      channelCount: 1, channelCountMode: 'explicit', processorOptions: {chunk: this.settings.chunk},
    });
    this.processor.port.onmessage = ({data}) => {
      if (this.closed || this.failure) return;
      if (this.queued >= 12) {
        this.failure = Error('摄像头上传出现积压，会话已结束。');
        onFailure(this.failure);
        return;
      }
      this.sent += data.byteLength / 2;
      const frame = this.sent >= this.nextFrame ? this.picture() : null;
      if (frame) this.nextFrame += this.settings.rate / this.settings.framesPerSecond;
      this.queued++;
      this.pending = this.pending.then(async () => {
        if (this.failure || this.serverFinished) return;
        const header = new ArrayBuffer(4);
        new DataView(header).setUint32(0, data.byteLength, true);
        const picture = await frame;
        const body = new Blob(picture ? [header, data, picture] : [header, data]);
        const response = await fetch(`/api/jobs/${job}/capture`, {method: 'POST', body, signal: AbortSignal.timeout(8000)});
        const status = await response.json();
        if (!response.ok) throw Error(status.detail || '摄像头上传失败。');
        this.serverFinished = status.finished;
        if (status.finished && !this.closed) onLimit();
      }).catch(error => {
        this.failure = error;
        onFailure(error);
      }).finally(() => { this.queued--; });
    };
    if (this.recorder) {
      const chunks = [];
      this.recording = new Promise(resolve => {
        this.recorder.ondataavailable = ({data}) => { if (data.size) chunks.push(data); };
        this.recorder.onstop = () => resolve(new Blob(chunks, {type: this.recorder.mimeType}));
        this.recorder.onerror = () => resolve(null);
        try { this.recorder.start(1000); } catch { resolve(null); }
      });
    }
    this.source.connect(this.processor).connect(this.audio.destination);
  }
  picture() {
    const video = this.video;
    const scale = Math.min(1, this.settings.side / Math.max(video.videoWidth, video.videoHeight));
    this.canvas.width = Math.max(2, Math.floor(video.videoWidth * scale));
    this.canvas.height = Math.max(2, Math.floor(video.videoHeight * scale));
    this.canvas.getContext('2d').drawImage(video, 0, 0, this.canvas.width, this.canvas.height);
    return new Promise(resolve => this.canvas.toBlob(resolve, 'image/jpeg', .8));
  }
  async close(onUpload) {
    if (this.closed) return this.ending;
    this.closed = true;
    this.ending = this.finish(onUpload);
    return this.ending;
  }
  async finish(onUpload) {
    this.processor?.disconnect();
    this.source?.disconnect();
    if (this.recorder?.state === 'recording') this.recorder.stop();
    this.stream.getTracks().forEach(track => track.stop());
    this.video.srcObject = null;
    this.video.muted = false;
    this.video.controls = true;
    await this.audio.close();
    await this.pending;
    if (this.job) {
      const recording = await this.recording;
      const url = `/api/jobs/${this.job}/stop`;
      try {
        const response = recording?.size
          ? await uploadWithProgress(url, recording, onUpload, 30000)
          : await fetch(url, {method: 'POST', signal: AbortSignal.timeout(30000)});
        if (!response.ok) throw Error('连续回放上传失败，已切换到抽帧回放。');
      } catch (error) {
        await fetch(url, {method: 'POST', signal: AbortSignal.timeout(5000)});
        throw error;
      }
    }
  }
}
