'use strict';
const config = JSON.parse(document.querySelector('#app-data').textContent);
const $ = (id) => document.getElementById(id);
const conditions = {T_neutral:'仅文字', T_asr:'原始转写', 'T+A':'加声音', 'T+V':'加画面', 'T+V+A':'声音＋画面', 'T+V+A_sep':'音画修订', 'T+A_shuf':'错配声音', 'T+V_shuf':'错配画面', final:'最终字幕', quick:'快速译文'};
const modes = {compare:'对比', guided:'引导', perceive:'先感知后翻译'};
const directionText = {ja2zh:'日语 → 中文', zh2ja:'中文 → 日语'};
let runs = [], active = null, selected = null, currentTab = config.defaultPanel;
let mockBackend = false, liveRows = new Map(), pendingPlay = null;
let camera = null, captureSettings = null;
let replayTrack = null, replayClip = null, liveTrack = null;
let dubbingAvailable = false, qwenAsrAvailable = false, speakerAnalysisAvailable = false;
let jobProgress = null;
const dubPolls = new Map();
const speakerPolls = new Map();
const mediaLoads = new WeakMap();

function clearMedia(video) {
  const load = mediaLoads.get(video);
  load?.controller.abort();
  video.pause();
  video.removeAttribute('src');
  video.load();
  if (load?.objectUrl) URL.revokeObjectURL(load.objectUrl);
  mediaLoads.delete(video);
}

function loadMedia(video, source, timeoutMs = 90000) {
  const current = mediaLoads.get(video);
  if (current?.source === source) return current.ready;
  clearMedia(video);
  video.defaultPlaybackRate = video.playbackRate = 1;
  const load = {source, controller: new AbortController()};
  mediaLoads.set(video, load);
  if (location.protocol === 'file:') {
    video.src = source;
    load.ready = Promise.resolve(true);
    return load.ready;
  }
  load.ready = (async () => {
    const timeout = setTimeout(() => load.controller.abort(), timeoutMs);
    try {
      const response = await fetch(source, {signal: load.controller.signal});
      if (!response.ok) throw Error(`视频下载失败（${response.status}）`);
      const blob = await response.blob();
      if (mediaLoads.get(video) !== load) return false;
      load.objectUrl = URL.createObjectURL(blob);
      video.src = load.objectUrl;
      return true;
    } catch (error) {
      if (mediaLoads.get(video) !== load) return false;
      mediaLoads.delete(video);
      throw error.name === 'AbortError' ? Error('视频下载超时') : error;
    } finally {
      clearTimeout(timeout);
    }
  })();
  return load.ready;
}

async function loadReplay(seconds, play = false) {
  const video = $('replay-video');
  const source = selectedDub()?.video || (replayClip ? `${active.base}/${replayClip.clip.path_preview}` : active?.video);
  if (!source) return;
  const format = $('replay-compat');
  const compatibility = !$('replay-format').hidden && format.checked;
  const url = compatibility ? `${source}?compat=1` : source;
  const status = $('replay-buffer');
  const retry = $('retry-replay');
  retry.hidden = true;
  if (location.protocol !== 'file:' && mediaLoads.get(video)?.source !== url) {
    status.textContent = compatibility ? '正在加载兼容视频' : '正在缓冲视频';
    status.hidden = false;
  }
  try {
    if (!await loadMedia(video, url)) return;
    status.hidden = true;
    if (seconds != null) video.currentTime = seconds;
    if (play) await video.play().catch(() => {});
  } catch (error) {
    if (compatibility) {
      format.checked = false;
      return loadReplay(seconds, play);
    }
    status.textContent = error.message;
    status.hidden = retry.hidden = false;
  }
}

function element(tag, text, classname) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (classname) node.className = classname;
  return node;
}
function option(value, text) { const o = element('option', text); o.value = value; return o; }
function time(ms) {
  const secs = Math.floor(ms / 1000);
  return `${String(Math.floor(secs / 60)).padStart(2, '0')}:${String(secs % 60).padStart(2, '0')}.${Math.floor(ms % 1000 / 100)}`;
}
function observation(container, info, title = '') {
  if (!info) return;
  const details = element('details');
  details.append(element('summary', `${title}模型观察，未核实${info.tone ? ` · ${info.tone}${info.strength ? `（${info.strength}）` : ''}` : ''}`));
  details.append(element('p', info.text));
  container.append(details);
}
function lineText(container, title, value, changed = false) {
  if (!value) return;
  const p = element('p');
  p.append(element('span', title, 'label'), element('span', value, changed ? 'changed' : ''));
  container.append(p);
}
function asrReview(container, review) {
  if (!review) return;
  const detail = element('details');
  const label = review.model ? 'Qwen3-ASR 复核' : '上下文复核';
  detail.append(element('summary', label + (review.applied ? '已修正识别' : '后保留初次识别')));
  lineText(detail, '初次识别', review.original);
  if (review.candidate && review.candidate !== review.original) lineText(detail, '复核候选', review.candidate);
  container.append(detail);
}
function speakerRow(run, row) {
  return run?.speaker_analysis?.lines?.[row.line_id || row.sample_id?.split('-').at(-1)];
}
function speakerLabel(info) {
  if (!info) return '';
  if (info.speaker_ids.length > 1) return '多人／待确认';
  return info.speaker_ids.length ? `说话人 ${info.speaker_ids[0]}` : '说话人待确认';
}
function showSpeakerDetail(container, info) {
  if (!info) return;
  const detail = element('div', null, 'speaker-detail');
  lineText(detail, '人物', speakerLabel(info));
  if (info.speaker_ids.length === 1) {
    const face = info.face;
    lineText(detail, '画面人物', face ? `口型匹配 · ${{left:'左侧',center:'中央',right:'右侧'}[face.side]}` : '画面对应待确认');
    if (face && active.video) {
      const seek = element('button', '定位口型画面', 'text-link');
      seek.type = 'button';
      seek.onclick = () => {
        replayClip = null;
        $('full-video').hidden = true;
        updateReplayCaptions();
        void loadReplay(face.time_ms / 1000);
      };
      detail.append(seek);
    }
    const pitch = info.pitch;
    lineText(detail, '语调', pitch?.median_f0_hz
      ? [
        {rising:'末段上扬',falling:'末段下降',level:'末段较平'}[pitch.pitch_trend],
        `基频中位 ${pitch.median_f0_hz} Hz`, `音高跨度 ${pitch.pitch_range_st} 半音`,
        pitch.relative_to_speaker_st !== undefined ? `比本人基线 ${pitch.relative_to_speaker_st >= 0 ? '+' : ''}${pitch.relative_to_speaker_st} 半音` : '',
      ].filter(Boolean).join(' · ')
      : '有声信息不足');
    if (info.chars_per_second !== undefined) {
      const pace = {faster:'较本人常速快',slower:'较本人常速慢',typical:'接近本人常速'}[info.pace];
      lineText(detail, '语速', [`${info.chars_per_second} 字/秒`, pace].filter(Boolean).join(' · '));
    }
    if (info.tone) lineText(detail, '模型语气，未核实', [info.tone.tone, info.tone.strength].filter(Boolean).join(' · '));
  }
  lineText(detail, '用语风格', info.wording?.join(' · '));
  container.append(detail);
}
function ruleText(checks) {
  const labels = [checks.question ? '问句' : '非问句'];
  if (checks.style) labels.push({honorific:'尊敬语', polite:'敬体', plain:'常体', none:'无谓语'}[checks.style]);
  if (checks.uses_nin) labels.push('使用「您」');
  if (checks.simplified_leak) labels.push('简体字混入');
  if (checks.emotion_aside) labels.push('括号情绪');
  if (checks.tone_punctuation_warning) labels.push('语气标点待核查');
  return labels.join(' · ');
}
function openTab(tab) {
  if (tab !== currentTab) {
    const video = $('live-video');
    if (currentTab === 'replay') $('replay-video').pause();
    if (currentTab === 'live' && video.controls) video.pause();
    if (!video.controls && !video.srcObject) video.muted = tab !== 'live' || $('live-mute').checked;
  }
  currentTab = tab;
  $('live').hidden = tab !== 'live'; $('replay').hidden = tab !== 'replay';
  $('experiment').hidden = tab !== 'experiment';
  $('mock-notice').hidden = !((tab === 'replay' && active?.mock) || (tab === 'live' && mockBackend));
  document.querySelectorAll('[data-tab]').forEach(button => {
    if (button.dataset.tab === tab) button.setAttribute('aria-current','page');
    else button.removeAttribute('aria-current');
  });
  if (tab === 'replay') void loadReplay();
}
function showExperiment(data) {
  if (!data?.rows.length) return;
  const strata = {main:'主表', japanese_onscreen:'画面有日文', chinese_onscreen:'画面可能有中文', no_chinese_onscreen:'画面未检出中文', chinese_question_onscreen:'画面中文含问号'};
  const labels = {recall:'问句召回率', false_positive:'误判为问句', f1:'问句 F1', style:'文体一致率', plain_baseline:'常体基线', kappa:'Cohen’s κ', chrf:'chrF', comet:'COMET', fallback:'回退率', simplified_leak:'简体字混入率', Q1:'Q1 附加问', Q2:'Q2 语气词', Q3:'Q3 正反问', Q4:'Q4 疑问代词', Q5:'Q5 无文字标记', mismatch_follow:'错配输入影响'};
  const stratum = $('experiment-stratum'), metric = $('experiment-metric');
  $('experiment-meta').textContent = `${data.count} 段视频 · ${data.run}`;
  stratum.replaceChildren(...Object.keys(strata).filter(key => data.rows.some(row => row.stratum === key)).map(key => option(key, strata[key])));
  const render = () => {
    const table = element('table');
    const head = table.createTHead().insertRow();
    ['条件','样本数','数值','95% 置信区间'].forEach(text => head.append(element('th', text)));
    const body = table.createTBody();
    for (const row of data.rows.filter(row => row.stratum === stratum.value && row.metric === metric.value)) {
      const interval = row.ci_low ? `${row.ci_low} 至 ${row.ci_high}` : '—';
      const tr = body.insertRow();
      const label = row.condition === 'T_asr' ? '原始转写 · 按文档或近似对齐' : conditions[row.condition] || row.condition;
      [label, row.n, row.value, interval].forEach(text => { tr.insertCell().textContent = text; });
    }
    $('experiment-table').replaceChildren(table);
  };
  stratum.onchange = () => {
    const previous = metric.value;
    const keys = Object.keys(labels).filter(key => data.rows.some(row => row.stratum === stratum.value && row.metric === key));
    metric.replaceChildren(...keys.map(key => option(key, labels[key])));
    if (keys.includes(previous)) metric.value = previous;
    render();
  };
  metric.onchange = render;
  stratum.onchange();
  $('experiment-figures').replaceChildren(...data.figures.map(figure => {
    const image = element('img'); image.alt = figure.label; image.src = figure.path; image.loading = 'lazy'; return image;
  }));
  document.querySelector('[data-tab="experiment"]').hidden = false;
}
function selectLine(row, seek = false) {
  selected = row;
  const node = $('selected-line'); node.replaceChildren();
  if (!row) { node.append(element('p','当前结果没有句子。','empty')); return; }
  $('selected-id').textContent = row.sample_id?.split('-').at(-1) || row.line_id;
  if (selectedDub()?.lines.includes(row)) {
    lineText(node, '原文', row.text_asr);
    lineText(node, '配音译文', row.multimodal);
    asrReview(node, row.asr_recheck);
    if (row.tone) lineText(node, '原声语气', [row.tone, row.strength].filter(Boolean).join(' · '));
    if (row.intonation) lineText(node, '原声语调', row.intonation.status === 'applied' ? '已跟随原声升降走势' : '沿用基础配音语调');
    return;
  }
  showSpeakerDetail(node, speakerRow(active, row));
  if (active.kind === 'realtime') {
    lineText(node, '原文', row.text_asr);
    lineText(node, '快速译文', row.quick);
    lineText(node, '音画修订', row.skipped ? '已跳过' : row.multimodal, row.changed);
    asrReview(node, row.asr_recheck);
    observation(node, row.observation);
  } else {
    lineText(node, active.transcript_kind === 'controlled' ? '固定台词' : '识别原文', row.text.text_asr);
    lineText(node, '中性化', row.text.text_neutral);
    asrReview(node, row.asr_recheck);
    if (active.pair) {
      lineText(node, '预期读法', active.pair.expected_reading);
      if (!Object.keys(active.pair.reviews || {}).length || active.pair.review !== '待人工核对') {
        lineText(node, '总判定', active.pair.review);
      }
    }
    if (row.asr_warning) node.append(element('p','识别可信度待核查','star'));
    for (const condition of active.conditions) {
      const output = row.outputs[condition];
      const label = conditions[condition] || condition;
      lineText(node, label, output.text, row.compare?.[condition]?.different);
      const verdict = active.pair?.reviews?.[condition];
      if (verdict) lineText(node, `${label} · 人工判定`, verdict);
      observation(node, output.observation, `${label} · `);
    }
    if (seek && !selectedDub()) {
      const button = $('full-video'); button.hidden = false;
      replayClip = row;
      updateReplayCaptions();
      void loadReplay(0, true);
    }
  }
  if (seek && active.video && (active.kind === 'realtime' || selectedDub())) {
    const start = row.start_ms ?? row.utterance.t_start_abs_ms;
    void loadReplay(start / 1000, true);
  }
  renderTable();
}
function renderTable() {
  if (!active) return;
  const realtime = active.kind === 'realtime';
  const table = element('table');
  const head = table.createTHead().insertRow();
  const columns = realtime ? ['时间','原文','快速译文','音画修订'] : ['片段','原文', ...active.conditions.map(c => conditions[c] || c)];
  columns.forEach(label => head.append(element('th', label)));
  const body = table.createTBody();
  const filtered = active.lines.filter(row => !$('changed-only').checked || (realtime ? row.changed : Object.values(row.compare || {}).some(cell => cell.different)));
  for (const row of filtered) {
    const tr = body.insertRow(); tr.setAttribute('aria-selected', String(row === selected));
    const button = element('button', time(realtime ? row.start_ms : row.utterance.t_start_abs_ms), 'seek');
    button.type = 'button'; button.onclick = () => selectLine(row, true); tr.insertCell().append(button);
    const original = realtime ? row.text_asr : row.text.text_asr;
    const label = speakerLabel(speakerRow(active, row));
    tr.insertCell().textContent = label ? `${label} · ${original}` : original;
    if (realtime) {
      tr.insertCell().textContent = row.quick;
      tr.insertCell().append(element('span', row.skipped ? '已跳过' : row.multimodal, row.changed ? 'changed' : ''));
      continue;
    }
    for (const condition of active.conditions) {
      const cell = tr.insertCell();
      const diff = row.compare?.[condition];
      if (diff?.parts?.some(part => part.changed)) diff.parts.forEach(part => cell.append(element('span', part.text, part.changed ? 'changed' : '')));
      else cell.append(element('span', row.outputs[condition].text, diff?.different ? 'changed' : ''));
      cell.append(element('small', ruleText(row.checks[condition])));
    }
  }
  $('compare-table').replaceChildren(filtered.length ? table : element('p', '当前筛选没有句子。','empty'));
  $('table-title').textContent = realtime ? '两阶段字幕记录' : '译文对比';
}
function updateDownload() {
  const link = $('download');
  const url = selectedDub()?.subtitles || active?.download[$('download-condition').value];
  link.hidden = !url; if (url) link.href = url;
  updateReplayCaptions();
  renderReplayLines();
}
function captionTrack(video, track, rows, condition, realtime, offset = 0, enabled = true, run = null) {
  if (!window.VTTCue || !video.addTextTrack) return null;
  track ??= video.addTextTrack('subtitles', '中日双语');
  track.mode = 'hidden';
  for (const cue of Array.from(track.cues)) track.removeCue(cue);
  for (const row of rows) {
    const start = realtime ? row.start_ms : row.utterance.t_start_abs_ms;
    const end = realtime ? row.end_ms : row.utterance.t_end_abs_ms;
    const original = realtime ? row.text_asr : row.text.text_asr;
    const translation = realtime ? (condition === 'quick' ? row.quick : row.multimodal || row.quick) : row.outputs[condition]?.text;
    if (end <= start || !translation) continue;
    const label = speakerLabel(speakerRow(run, row));
    const text = `${label ? `[${label}] ` : ''}${original}\n${translation}`.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
    track.addCue(new VTTCue(Math.max(0, start - offset) / 1000, (end - offset) / 1000, text));
  }
  track.mode = enabled ? 'showing' : 'disabled';
  return track;
}
function updateReplayCaptions() {
  const video = $('replay-video');
  const supported = Boolean(active?.video && window.VTTCue && video.addTextTrack);
  $('replay-caption').hidden = !supported;
  if (!supported) {
    if (replayTrack) replayTrack.mode = 'disabled';
    return;
  }
  const dub = selectedDub();
  if (dub) {
    replayTrack = captionTrack(video, replayTrack, dub.lines, 'final', true, 0, $('captions').checked);
    return;
  }
  replayTrack = captionTrack(video, replayTrack, replayClip ? [replayClip] : active.lines, $('download-condition').value,
    active.kind === 'realtime', replayClip?.clip.t_start_abs_ms ?? 0, $('captions').checked, active);
}
function selectedDub() {
  return active?.dub?.status === 'complete' && $('replay-version').value === 'dub' ? active.dub : null;
}
function showDub() {
  const dub = active?.dub;
  const ready = dub?.status === 'complete';
  const busy = dub?.status === 'running';
  const prosody = dub?.prosody;
  $('dub-switch').hidden = !ready;
  if (ready) {
    $('dub-download').href = dub.video;
    $('replay-version').options[0].textContent = dub.language === 'ja' ? '日语 · AI 合成' : '中文 · AI 合成';
  }
  $('dub-form').hidden = !dubbingAvailable || !active?.can_dub || ready || busy;
  $('dub-status').hidden = !busy && dub?.status !== 'failed' && !(ready && prosody);
  $('dub-status').textContent = ready && prosody
    ? `原声语调已应用 ${prosody.applied_lines}/${prosody.total_lines} 句`
    : busy && dub.total ? `${dub.message}（${dub.completed}/${dub.total} 句）` : dub?.message || '正在准备配音';
  $('dub-progress').hidden = !busy;
  setProgressValue($('dub-progress'), dub?.completed, dub?.total);
  $('replay-condition').hidden = !active || (active.kind === 'realtime' && !active.download.quick) || Boolean(selectedDub());
}
async function updateDub(id) {
  const dub = await readServerStatus(`/api/runs/${id}/dub`);
  if (!dub) return null;
  const run = runs.find(item => item.id === id);
  if (run) run.dub = dub;
  if (active?.id === id) {
    if (dub.status === 'complete') {
      $('replay-version').value = 'dub';
      replayClip = null;
      $('full-video').hidden = true;
      updateDownload();
      if (currentTab === 'replay') void loadReplay();
    }
    showDub();
  }
  return dub;
}
function watchDub(id) {
  if (dubPolls.has(id)) return dubPolls.get(id);
  const poll = (async () => {
    try {
      while (true) {
        const dub = await updateDub(id);
        if (dub && !['idle', 'running'].includes(dub.status)) break;
        if (!dub && active?.id === id) {
          $('dub-status').textContent = '暂时无法读取配音进度，正在重试连接';
          $('dub-status').hidden = false;
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    } finally {
      dubPolls.delete(id);
    }
  })();
  dubPolls.set(id, poll);
  return poll;
}
function showSpeakers() {
  const analysis = active?.speaker_analysis;
  const busy = analysis?.status === 'running';
  const done = analysis?.status === 'complete';
  const canAddTone = done && analysis.model_tone?.status !== 'complete' &&
    Object.values(analysis.lines).some(row => row.speaker_ids.length === 1 && row.speech_ms && !row.tone);
  $('speaker-panel').hidden = !analysis && !(speakerAnalysisAvailable && active?.can_analyze_speakers);
  const button = $('analyze-speakers');
  button.hidden = (done && !canAddTone) || !speakerAnalysisAvailable || !active?.can_analyze_speakers;
  button.disabled = busy;
  button.textContent = canAddTone ? '补充模型语气' : analysis?.status === 'failed' ? '重试人物分析' : '分析人物和语气';
  $('speaker-tone-option').hidden = done || busy;
  $('speaker-progress').hidden = !busy;
  setProgressValue($('speaker-progress'), analysis?.completed, analysis?.total);
  const status = $('speaker-status');
  status.hidden = !analysis;
  if (done) {
    const uncertain = Object.values(analysis.lines).filter(row => row.speaker_ids.length !== 1).length;
    status.textContent = [
      `区分 ${analysis.speakers.length} 位说话人`,
      uncertain ? `${uncertain} 句说话人待确认` : '',
      analysis.model_tone ? `模型语气 ${analysis.model_tone.labeled}/${analysis.model_tone.eligible} 句` : '',
      analysis.vision_status !== 'complete' ? '画面口型分析未完成' : '',
    ].filter(Boolean).join(' · ');
  } else {
    const count = analysis?.total ? `（${analysis.completed}/${analysis.total} ${analysis.unit || '句'}）` : '';
    status.textContent = analysis ? `${analysis.message || '正在分析人物'}${count}` : '';
  }
  const summary = $('speaker-summary');
  summary.replaceChildren(...(analysis?.speakers || []).map(person => element('li',
    [`说话人 ${person.id} · ${person.lines} 句`, person.face_lines ? `口型匹配 ${person.face_lines} 句` : '',
      person.typical_f0_hz ? `${person.typical_f0_hz} Hz` : ''].filter(Boolean).join(' · '))));
  summary.hidden = !analysis?.speakers?.length;
  const download = $('speaker-download');
  download.hidden = !done;
  if (done) download.href = `${active.base}/speakers.json`;
}
async function watchSpeakers(id) {
  if (speakerPolls.has(id)) return speakerPolls.get(id);
  const task = (async () => {
    while (true) {
      const result = await readServerStatus(`/api/runs/${id}/speakers`);
      const run = runs.find(item => item.id === id);
      if (!run) break;
      if (result) {
        run.speaker_analysis = result;
        if (active?.id === id) {
          showSpeakers();
          if (result.status === 'complete') {
            if (selected) selectLine(selected);
            renderReplayLines();
            updateReplayCaptions();
          }
        }
        if (['complete','failed'].includes(result.status)) break;
      } else if (active?.id === id) {
        $('speaker-status').textContent = '暂时无法读取人物分析进度，正在重试连接';
      }
      await new Promise(resolve => setTimeout(resolve, 750));
    }
  })().catch(error => {
    const run = runs.find(item => item.id === id);
    if (!run) return;
    run.speaker_analysis = {status:'failed', message:error.message};
    if (active?.id === id) showSpeakers();
  }).finally(() => speakerPolls.delete(id));
  speakerPolls.set(id, task);
  return task;
}
function renderReplayLines() {
  const list = $('replay-lines');
  if (!active) { list.replaceChildren(); return; }
  const realtime = active.kind === 'realtime';
  const condition = $('download-condition').value;
  const dub = selectedDub();
  const lines = dub?.lines || active.lines;
  if (selected && !lines.includes(selected)) selectLine(lines[0]);
  list.replaceChildren(...lines.map(row => {
    const button = element('button', null, 'transcript-row');
    button.type = 'button';
    const start = dub || realtime ? row.start_ms : row.utterance.t_start_abs_ms;
    const text = dub || realtime ? row.text_asr : row.text.text_asr;
    const translation = dub || realtime ? (condition === 'quick' && !dub ? row.quick : row.multimodal || row.quick) : row.outputs[condition]?.text;
    const label = dub ? '' : speakerLabel(speakerRow(active, row));
    button.append(element('time', time(start)), element('strong', translation || text), element('span', label ? `${label} · ${text}` : text));
    button.onclick = () => {
      selectLine(row);
      list.querySelector('[aria-current]')?.removeAttribute('aria-current');
      button.setAttribute('aria-current', 'true');
      if (!active.video) return;
      if (replayClip) {
        replayClip = null;
        $('full-video').hidden = true;
        updateReplayCaptions();
      }
      void loadReplay(start / 1000);
    };
    return button;
  }));
}
function selectRun(id) {
  const video = $('replay-video');
  const changed = active?.id !== id;
  if (changed || replayClip) {
    clearMedia(video);
    $('replay-buffer').hidden = $('retry-replay').hidden = true;
  }
  active = runs.find(run => run.id === id) || null;
  if (changed) {
    $('replay-version').value = 'dub';
    $('dub-form').reset();
    $('speaker-tone-model').checked = true;
  }
  replayClip = null;
  $('no-replay').hidden = Boolean(active); $('replay-content').hidden = !active;
  $('download-condition').disabled = !active; $('run-select').disabled = !active;
  $('download-condition').replaceChildren(); $('changed-only').checked = false;
  $('replay-metrics').hidden = !active?.summary;
  const counterpart = active?.pair && runs.find(run =>
    run.pair?.batch === active.pair.batch && run.pair.pair_id === active.pair.pair_id && run.id !== active.id);
  const switchPair = $('switch-pair');
  switchPair.hidden = !counterpart;
  if (counterpart) {
    switchPair.textContent = `查看 ${counterpart.pair.variant} 版`;
    switchPair.onclick = () => { $('run-select').value = counterpart.id; selectRun(counterpart.id); };
  }
  if (active) {
    Object.keys(active.download).forEach(key => $('download-condition').append(option(key, conditions[key] || key)));
    $('download-condition').value = active.download['T+V+A'] ? 'T+V+A' : Object.keys(active.download)[0];
    const audioLabel = active.source_kind === 'webcam' && active.replay_quality !== 'continuous' ? '抽帧回放 · 麦克风原声' : '原声';
    $('replay-meta').textContent = `${audioLabel} · ${directionText[active.direction]} · ${modes[active.mode]}`;
    if (active.asr_qwen) {
      $('replay-meta').textContent += active.asr_qwen.status === 'complete'
        ? ` · Qwen3-ASR 修正 ${active.asr_qwen.applied}/${active.asr_qwen.total} 句`
        : ' · Qwen3-ASR 复核未完成，已使用 Whisper';
    }
    $('replay-meta').title = `${active.model.model} · ${active.id}`;
    const credit = $('attribution'); credit.hidden = !active.attribution;
    if (active.attribution) { credit.href = active.attribution.url; credit.textContent = active.attribution.text; }
    video.hidden = $('replay-video-download').hidden = !active.video;
    $('replay-format').hidden = !active.video || config.runs !== null ||
      !video.canPlayType('video/webm; codecs="vp8, opus"');
    if (active.video) $('replay-video-download').href = active.video;
    $('full-video').hidden = true;
    if (active.summary) {
      metricsCards($('replay-metrics-values'), active.summary);
      $('replay-csv').href = active.latency_csv;
    }
    selectLine(active.lines[0]);
  }
  showDub(); showSpeakers(); updateDownload(); openTab(currentTab);
  if (active?.speaker_analysis?.status === 'running') void watchSpeakers(id);
  if (active?.dub?.status === 'running') {
    void watchDub(id).catch(error => {
      if (active?.id !== id) return;
      $('dub-status').textContent = error.message;
    });
  }
}
async function refreshRuns(preferred) {
  runs = config.runs || await fetch('/api/runs').then(response => response.json());
  const previous = preferred || $('run-select').value;
  $('run-select').replaceChildren(...runs.map(run => option(run.id, `${run.name} · ${directionText[run.direction]} · ${modes[run.mode]}${run.mock ? ' · Mock' : ''}`)));
  if (runs.some(run => run.id === previous)) $('run-select').value = previous;
  selectRun($('run-select').value);
}
function state(message, error = false) {
  $('status').textContent = message; $('status').classList.toggle('error', error);
}
function setProgressValue(bar, completed, total) {
  if (Number.isFinite(completed) && Number.isFinite(total) && total > 0) {
    bar.max = total;
    bar.value = Math.min(completed, total);
    return Math.floor(100 * bar.value / total);
  }
  bar.removeAttribute('value');
  return null;
}
function showJobProgress(message, completed, total, unit) {
  const now = performance.now();
  if (!jobProgress || jobProgress.message !== message) {
    jobProgress = {...jobProgress, message, started:now, updated:now};
    $('job-phase').textContent = message;
  }
  const percent = setProgressValue($('job-bar'), completed, total);
  const count = unit && total > 0 ? `${completed}/${total} ${unit} · ` : '';
  $('job-percent').textContent = percent == null ? '' : `${count}${percent}%`;
  if (completed !== jobProgress.completed) jobProgress.updated = now;
  jobProgress.completed = completed;
  $('job-bar').hidden = $('job-progress').hidden = false;
  renderJobDetail();
}
function renderJobDetail() {
  if (!jobProgress || jobProgress.finished) return;
  const now = performance.now();
  const seconds = Math.floor((now - jobProgress.started) / 1000);
  const unchanged = Math.floor((now - jobProgress.updated) / 1000);
  const connected = now - jobProgress.connectedAt < 16000;
  const stale = unchanged >= 45;
  const status = jobProgress.retrying ? '连接暂时中断，正在重试' : stale
    ? (connected ? `服务已响应，${unchanged} 秒未更新进度` : `${unchanged} 秒未更新进度`)
    : (connected ? '服务连接正常' : '');
  $('job-progress').dataset.state = jobProgress.retrying || stale ? 'waiting' : '';
  $('job-progress-detail').textContent = [`本步骤已用 ${seconds} 秒`, status].filter(Boolean).join(' · ');
}
function finishJobProgress(message, failed = false, detail = failed ? '可检查错误后重新提交' : '可在结果回放查看') {
  showJobProgress(message, 1, 1);
  jobProgress.finished = true;
  $('job-progress').dataset.state = failed ? 'error' : 'complete';
  $('job-progress-detail').textContent = detail;
  $('job-bar').hidden = failed;
  if (failed) $('job-percent').textContent = '';
}
function uploadWithProgress(url, body, onUpload, timeout = 0) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', url);
    request.timeout = timeout;
    request.upload.onprogress = event => {
      if (event.lengthComputable) onUpload?.(event.loaded, event.total);
    };
    request.onload = () => resolve(new Response(request.responseText || null, {status:request.status}));
    request.onerror = () => reject(Error('上传连接已中断，请重试'));
    request.ontimeout = () => reject(Error('上传等待超时，请重试'));
    request.send(body);
  });
}
async function readServerStatus(url) {
  const response = await fetch(url, {cache:'no-store', signal:AbortSignal.timeout(8000)}).catch(() => null);
  if (!response || response.status >= 500 || response.status === 429) return null;
  if (response.status === 404) throw Error('找不到此任务，服务可能已重启。请重新提交。');
  if (!response.ok) throw Error(`会话状态读取失败（${response.status}）`);
  return response.json().catch(() => null);
}
function pollConnection(available) {
  if (available) jobProgress.connectedAt = performance.now();
  jobProgress.retrying = !available;
  renderJobDetail();
}
function renderLive(row) {
  const previous = liveRows.get(row.line_id);
  liveRows.set(row.line_id, row);
  const list = $('live-lines');
  const follow = list.scrollHeight - list.scrollTop - list.clientHeight < 48;
  const node = element('section', null, 'sentence');
  node.id = `live-${row.line_id}`;
  node.append(element('time', `${time(row.start_ms)} – ${time(row.end_ms)}`));
  if (row.multimodal || row.quick) node.append(element('strong', row.multimodal || row.quick, 'translation'));
  node.append(element('p', row.text_asr, 'original'));
  asrReview(node, row.asr_recheck);
  if (row.changed || row.observation) {
    const detail = element('details'); detail.append(element('summary', '翻译详情'));
    if (row.changed) lineText(detail, '快速译文', row.quick);
    observation(detail, row.observation);
    node.append(detail);
  }
  if (previous) $(node.id).replaceWith(node);
  else { if (liveRows.size === 1) list.replaceChildren(); list.append(node); }
  if (follow) list.scrollTop = list.scrollHeight;
  $('overlay').textContent = row.multimodal || row.quick || row.text_asr;
  $('overlay').hidden = false;
}
async function startPlayback() {
  const button = $('resume-play');
  const job = pendingPlay;
  if (!job || button.disabled) return;
  const video = $('live-video');
  button.disabled = true;
  try {
    if (mediaLoads.get(video)?.source !== job.source) {
      state('正在缓冲视频');
      showJobProgress('正在缓冲视频');
    }
    if (!await loadMedia(video, job.source, 60000)) return;
    try { await video.play(); }
    catch { button.hidden = false; state('等待播放'); showJobProgress('请点击「开始播放」继续'); return; }
    const response = await fetch(`/api/jobs/${job.id}/play`, {method:'POST'});
    if (!response.ok) throw Error('无法开始翻译会话');
    pendingPlay = null;
    button.hidden = true;
    state('正在按原速翻译');
    showJobProgress('正在按原速翻译');
  } catch (error) {
    video.pause();
    video.currentTime = 0;
    button.hidden = false;
    state(error.message, true);
    showJobProgress(`${error.message}，可点击「开始播放」重试`);
  } finally {
    button.disabled = false;
  }
}
function metricsCards(list, summary) {
  const stages = [['切句等待','vad_wait'],['任务排队','queue_wait'],['语音识别推理','asr_inference'],
    ['快译调用','quick_translation'],['媒体观察调用','observation'],['修订调用','multimodal_translation']];
  const metrics = [['原文',summary.asr],['快速译文',summary.quick],['音画修订',summary.multimodal],
    ...stages.filter(([,key]) => summary.stages?.[key]?.n).map(([title,key]) => [title,summary.stages[key]])];
  const cards = metrics.map(([title,values]) => {
    const card = element('div', null, 'metric');
    const value = values.median_ms == null ? '—' : `${(values.median_ms / 1000).toFixed(2)} s`;
    card.append(element('small',title), element('strong',value));
    if (values.p90_ms != null) card.append(element('small', `P90 ${(values.p90_ms / 1000).toFixed(2)} s`));
    return card;
  });
  const skip = element('div', null, 'metric');
  skip.append(element('small','音画跳过率'), element('strong', summary.skip_rate == null ? '—' : `${(summary.skip_rate * 100).toFixed(1)}%`));
  cards.splice(3, 0, skip);
  list.replaceChildren(...cards);
}
function renderMetrics(event) {
  metricsCards($('metrics-values'), event.summary);
  $('metrics-csv').href = `/files/${event.run_id}/session/latency_summary.csv`;
  $('live-srt').href = `/files/${event.run_id}/session/subtitles.srt`;
  $('metrics').hidden = $('live-srt').hidden = false;
}
async function poll(job, workflow, withDub) {
  let after = 0;
  let dubbingRun;
  let translated = 0;
  let remainingTotal = null;
  while (true) {
    const batch = await readServerStatus(`/api/jobs/${job}?after=${after}`);
    pollConnection(Boolean(batch));
    if (!batch) {
      await new Promise(resolve => setTimeout(resolve, 1500));
      continue;
    }
    after += batch.events.length;
    for (const event of batch.events) {
      switch (event.type) {
        case 'status':
        case 'progress':
          state(event.message);
          remainingTotal = event.stage === 'remaining' ? event.total : null;
          showJobProgress(
            event.message, remainingTotal == null ? event.completed : translated, event.total,
            remainingTotal == null ? '项' : '句',
          );
          break;
        case 'ready':
          if (event.webcam) {
            camera.job = job;
            if (camera.closed) {
              await fetch(`/api/jobs/${job}/stop`, {method:'POST'});
              break;
            }
            await fetch(`/api/jobs/${job}/play`, {method:'POST'});
            camera.start(job, error => { state(error.message, true); stopCamera().catch(() => {}); }, endRecording);
            state('正在录制');
            showJobProgress('正在录制并翻译');
            break;
          }
          const source = `/files/${event.run_id}/media/source.mp4`;
          pendingPlay = {id: job, source};
          const download = $('live-video-download');
          download.href = source;
          download.hidden = false;
          $('live-empty').hidden = true; $('live-video').hidden = false;
          $('live-sound').hidden = $('live-audio').hidden = false;
          await startPlayback(); break;
        case 'language': $('detected-language').textContent = directionText[event.direction]; break;
        case 'original':
        case 'quick':
          renderLive(event);
          jobProgress.updated = performance.now();
          break;
        case 'sentence':
        case 'no_speech':
          if (event.type === 'sentence') renderLive(event);
          translated++;
          if (remainingTotal != null) showJobProgress('正在完成剩余字幕', translated, remainingTotal, '句');
          break;
        case 'error':
          state(event.message, true);
          finishJobProgress(event.message, true);
          break;
        case 'dub':
          dubbingRun = event.run_id;
          showJobProgress('正在准备配音');
          break;
        case 'done':
          showJobProgress('字幕已生成，正在加载回放');
          if (camera?.failure) state(camera.failure.message, true);
          else state(event.summary?.utterances === 0 ? '会话结束，未检测到语音' : '翻译完成');
          if (event.summary) renderMetrics(event);
          await refreshRuns(event.run_id);
          if (active?.id === event.run_id) {
            $('open-result').hidden = false;
            $('open-result').onclick = () => { $('run-select').value = event.run_id; selectRun(event.run_id); openTab('replay'); };
            const video = $('live-video');
            if (active.video && workflow !== 'offline') {
              if (workflow === 'webcam') {
                video.muted = false;
                $('live-audio').textContent = active.replay_quality === 'continuous' ? '麦克风原声' : '麦克风原声 · 抽帧回放';
                const download = $('live-video-download');
                download.href = active.video;
                download.hidden = false;
                state('正在缓冲回放');
                try {
                  await loadMedia(video, active.video);
                  state('翻译完成');
                } catch (error) {
                  state(`${error.message}，可在结果回放重试。`, true);
                }
              }
              video.hidden = $('live-audio').hidden = false;
              $('live-empty').hidden = $('overlay').hidden = true;
              video.controls = true;
              liveTrack = captionTrack(video, liveTrack, active.lines, 'final', true);
            }
          }
          if (workflow === 'offline') openTab('replay');
          if (withDub) showJobProgress('字幕已生成，正在准备配音');
          else if (event.summary?.utterances === 0) {
            finishJobProgress('会话结束，未检测到语音', false, '可重新录制或选择包含语音的视频');
          } else finishJobProgress('处理完成');
          break;
      }
    }
    if (dubbingRun) {
      const dub = await updateDub(dubbingRun);
      pollConnection(Boolean(dub));
      if (dub?.status === 'complete') {
        state('配音完成'); finishJobProgress('字幕和配音已完成'); dubbingRun = null;
      } else if (dub?.status === 'failed') {
        state('字幕完成，配音生成失败');
        finishJobProgress(dub.message || '配音生成失败', true, '可在结果回放查看字幕或重试配音');
        dubbingRun = null;
      } else if (dub?.message) {
        state(dub.message);
        showJobProgress(dub.message, dub.completed, dub.total, '句');
      }
    }
    if (batch.finished && !dubbingRun) return;
    await new Promise(resolve => setTimeout(resolve, 220));
  }
}
async function stopCamera(onUpload) {
  $('stop-camera').hidden = true;
  if (camera && !camera.closed) await camera.close(onUpload);
}
function endRecording() {
  state('正在结束录制');
  showJobProgress('正在结束录制，准备上传回放');
  stopCamera((loaded, total) => {
    if (jobProgress?.finished) return;
    if (loaded >= total) showJobProgress('录像已发送，正在保存回放');
    else showJobProgress('正在上传录像', loaded, total);
  }).catch(error => {
    state(error.message, true);
    if (!jobProgress?.finished) showJobProgress('录像上传遇到问题，正在检查处理状态');
  });
}
function updateSource(form) {
  const source = form.elements.source.value;
  for (const kind of ['upload','sample','webcam']) {
    $(`${kind === 'webcam' ? 'camera' : kind}-source`).hidden = source !== kind;
  }
  $('workflow-setting').hidden = source === 'webcam';
  const qwen = qwenAsrAvailable && source !== 'webcam' && form.elements.workflow.value === 'offline';
  $('qwen-asr-setting').hidden = form.elements.qwen_recheck.disabled = !qwen;
  $('dub-request').hidden = !dubbingAvailable || source === 'sample';
}
async function submit(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const source = form.get('source');
  form.delete('source');
  if (source === 'sample') form.delete('dub');
  if (source !== 'upload' || !form.get('video')?.size) form.delete('video');
  if (source !== 'sample') form.delete('sample');
  if (source === 'webcam') form.set('workflow', 'webcam');
  const workflow = form.get('workflow');
  if (workflow !== 'webcam' && !form.has('video') && !form.get('sample')) { state('请选择视频或示例片段', true); return; }
  $('start').disabled = true; $('metrics').hidden = $('live-srt').hidden = $('overlay').hidden = true;
  $('open-result').hidden = $('live-audio').hidden = $('live-video-download').hidden = true;
  $('live-audio').textContent = '原声';
  $('detected-language').textContent = directionText[form.get('direction')] || '正在识别语向';
  const video = $('live-video');
  clearMedia(video); video.hidden = true; $('live-empty').hidden = false;
  if (liveTrack) liveTrack.mode = 'disabled';
  video.controls = false;
  video.playbackRate = 1;
  video.muted = $('live-mute').checked;
  $('live-lines').replaceChildren(element('p','等待识别第一句。','empty')); liveRows = new Map();
  state('正在提交');
  jobProgress = null;
  if (workflow === 'webcam') showJobProgress('正在打开摄像头与麦克风');
  else if (form.has('video')) showJobProgress('正在上传视频', 0, form.get('video').size);
  else showJobProgress('正在提交任务');
  const progressTimer = setInterval(renderJobDetail, 1000);
  try {
    if (workflow === 'webcam') {
      video.hidden = false; $('live-empty').hidden = true;
      camera = await WebcamCapture.open(captureSettings, video);
      if (camera.recorder) form.set('record_replay', '1');
      $('stop-camera').hidden = false;
    }
    const response = form.has('video')
      ? await uploadWithProgress('/api/jobs', form, (loaded, total) => {
        if (loaded >= total) showJobProgress('视频已发送，等待服务器确认');
        else showJobProgress('正在上传视频', loaded, total);
      })
      : await fetch('/api/jobs', {method:'POST', body:form});
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || '提交失败');
    if (camera) {
      camera.job = result.job;
      if (camera.closed) await fetch(`/api/jobs/${result.job}/stop`, {method:'POST'});
    }
    await poll(result.job, workflow, form.get('dub') === '1');
  } catch (error) {
    state(error.message, true);
    finishJobProgress(error.message, true);
  }
  finally {
    await stopCamera().catch(error => state(error.message, true));
    if (workflow === 'live') {
      pendingPlay = null;
      $('resume-play').hidden = $('live-sound').hidden = $('overlay').hidden = true;
      video.controls = !video.hidden;
    }
    clearInterval(progressTimer);
    camera = null; $('start').disabled = false;
  }
}
async function initialize() {
  document.querySelectorAll('[data-tab]').forEach(button => {
    if (button.dataset.tab === 'live') button.hidden = !config.live;
    button.onclick = () => openTab(button.dataset.tab);
  });
  showExperiment(config.experiment);
  $('run-select').onchange = () => selectRun($('run-select').value);
  $('download-condition').onchange = updateDownload;
  $('captions').onchange = () => {
    if (replayTrack) replayTrack.mode = $('captions').checked ? 'showing' : 'disabled';
  };
  $('replay-video').textTracks?.addEventListener('change', () => {
    if (replayTrack && replayTrack.mode !== 'hidden') $('captions').checked = replayTrack.mode === 'showing';
  });
  $('changed-only').onchange = renderTable;
  $('full-video').onclick = () => {
    replayClip = null;
    $('full-video').hidden = true;
    updateReplayCaptions();
    void loadReplay();
  };
  $('retry-replay').onclick = () => loadReplay();
  $('replay-compat').onchange = () => {
    const video = $('replay-video');
    void loadReplay(video.currentTime, !video.paused && !video.ended);
  };
  $('replay-version').onchange = () => {
    replayClip = null;
    $('full-video').hidden = true;
    showDub();
    updateDownload();
    void loadReplay(0);
  };
  $('analyze-speakers').onclick = async () => {
    const run = active;
    if (!run || run.speaker_analysis?.status === 'running') return;
    const modelTone = run.speaker_analysis?.status === 'complete' || $('speaker-tone-model').checked;
    run.speaker_analysis = {status:'running', message:'正在提交人物分析'};
    showSpeakers();
    try {
      const response = await fetch(`/api/runs/${run.id}/speakers?model_tone=${modelTone}`, {method:'POST'});
      const result = await response.json();
      if (!response.ok) throw Error(result.detail || '人物分析提交失败');
      run.speaker_analysis = result;
      if (active?.id === run.id) showSpeakers();
      await watchSpeakers(run.id);
    } catch (error) {
      run.speaker_analysis = {status:'failed', message:error.message};
      if (active?.id === run.id) showSpeakers();
    }
  };
  $('dub-form').onsubmit = async event => {
    event.preventDefault();
    const id = active.id;
    const form = event.currentTarget;
    const button = form.querySelector('button');
    button.disabled = true;
    try {
      const response = await fetch(`/api/runs/${id}/dub`, {method:'POST', body:new FormData(form)});
      if (!response.ok) throw Error((await response.json()).detail || '无法启动配音。');
      await watchDub(id);
    } catch (error) {
      if (active?.id === id) {
        $('dub-status').textContent = error.message;
        $('dub-status').hidden = false;
      }
    } finally { button.disabled = false; }
  };
  $('resume-play').onclick = startPlayback;
  $('live-mute').onchange = event => { $('live-video').muted = event.target.checked; };
  $('live-video').oncontextmenu = event => {
    if (!$('live-video').controls) event.preventDefault();
  };
  $('live-video').ontimeupdate = event => {
    const video = event.currentTarget;
    if (jobProgress?.message === '正在按原速翻译' && Number.isFinite(video.duration)) {
      if (video.ended) showJobProgress('播放结束，正在等待字幕');
      else showJobProgress('正在按原速翻译', video.currentTime, video.duration);
    }
  };
  $('stop-camera').onclick = endRecording;
  const form = $('job-form');
  if (config.webcam) form.elements.source.value = 'webcam';
  form.onsubmit = submit;
  form.onchange = () => updateSource(form);
  updateSource(form);
  form.elements.video.onchange = event => { $('file-name').textContent = event.target.files[0]?.name || 'MP4 · MOV · WebM'; };
  const materials = config.materials || [];
  $('classroom-materials').hidden = !materials.length;
  $('classroom-links').replaceChildren(...materials.map(item => {
    const entry = element('li');
    const link = element('a', item.label, 'text-link');
    link.href = item.path;
    entry.append(link);
    return entry;
  }));
  openTab(currentTab);
  await refreshRuns();
  if (!config.runs) {
    const options = await fetch('/api/options').then(response => response.json());
    dubbingAvailable = options.dubbing;
    qwenAsrAvailable = options.qwenAsr;
    speakerAnalysisAvailable = options.speakerAnalysis;
    showDub(); showSpeakers();
    updateSource(form);
    if (!config.live) return;
    mockBackend = options.backend === 'mock';
    captureSettings = options.capture;
    $('input-limit').textContent = `视频最长 ${options.maxSeconds} 秒`;
    for (const sample of options.samples) {
      $('sample').append(option(sample.id, sample.title));
    }
    $('start').disabled = false;
    openTab(currentTab);
  }
}
initialize().catch(error => { $('no-replay').textContent = error.message; $('no-replay').hidden = false; state(error.message, true); });
