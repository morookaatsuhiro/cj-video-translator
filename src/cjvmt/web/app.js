'use strict';
const config = JSON.parse(document.querySelector('#app-data').textContent);
const $ = (id) => document.getElementById(id);
const conditions = {T_neutral:'仅看文字', T_asr:'原始转写', 'T+A':'加声音', 'T+V':'加画面', 'T+V+A':'声音＋画面', 'T+V+A_sep':'音画修订', 'T+A_shuf':'错配声音', 'T+V_shuf':'错配画面', final:'最终字幕', quick:'快速译文'};
const modes = {compare:'对比', guided:'引导', perceive:'先感知后翻译'};
const directionText = {ja2zh:'日语 → 中文', zh2ja:'中文 → 日语'};
let runs = [], active = null, selected = null, currentTab = config.defaultPanel, currentSubTab = 'compare';
let historyFilter = 'all', historyQuery = '';
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
function shortRunTime(id) {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(id || '');
  return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : '';
}
function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
function friendlyRunTitle(run) {
  if (run.custom_title) return run.custom_title;
  const name = run.name || '';
  if (/^[0-9a-f]{16,}\./i.test(name) || name === 'source.mp4') return '自录上传视频';
  if (name === 'duo-gapped.mp4') return '双人对话示例';
  if (name === '摄像头直播') return '摄像头自录';
  return name.replace(/\.mp4$/i, '');
}
function friendlyRunSubtitle(run) {
  const tags = [directionText[run.direction]];
  if (run.dub?.status === 'complete') tags.push('AI 配音');
  else if (run.speaker_analysis?.status === 'complete') tags.push('声纹口型');
  else if (run.asr_qwen?.status === 'complete') tags.push('Qwen3-ASR');
  else if (run.kind === 'offline') tags.push(`${run.conditions?.length || 4} 条件`);
  const ts = shortRunTime(run.id);
  if (ts) tags.push(ts);
  if (run.size_bytes) tags.push(formatBytes(run.size_bytes));
  if (run.mock) tags.push('Mock');
  return tags.join(' · ');
}
function observation(container, info, title = '') {
  if (!info) return;
  const details = element('details');
  details.append(element('summary', `${title}模型观察（未核实）${info.tone ? ` · ${info.tone}${info.strength ? `（${info.strength}）` : ''}` : ''}`));
  if (info.object_evidence) lineText(details, '物体线索', info.object_evidence);
  const text = info.object_evidence ? info.text.replace(/^\s*5[.．、：:)].*$/m, '').trim() : info.text;
  details.append(element('p', text));
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
  detail.open = Boolean(review.applied);
  const label = review.model ? 'Qwen3-ASR 二次复听' : 'Whisper 上下文复听';
  detail.append(element('summary', label + (review.applied ? '：已修正识别' : '：保留初次识别')));
  lineText(detail, '初次识别', review.original);
  if (review.reasons?.length) lineText(detail, '判定依据', review.reasons.join('、'));
  if (review.candidate && review.candidate !== review.original) lineText(detail, '复听候选', review.candidate);
  if (review.adopted_text && review.applied) lineText(detail, '实际采用', review.adopted_text, true);
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
  if (!info) return false;
  lineText(container, '人物分组', speakerLabel(info));
  if (info.speaker_ids.length === 1) {
    const face = info.face;
    lineText(container, '画面定位', face ? `口型同步匹配 · ${{left:'左侧',center:'中央',right:'右侧'}[face.side]}` : '画面对应待确认');
    if (face && active.video) {
      const seek = element('button', '定位到口型画面 →', 'secondary compact-btn');
      seek.type = 'button';
      seek.style.margin = '4px 0 8px';
      seek.onclick = () => {
        replayClip = null;
        $('full-video').hidden = true;
        updateReplayCaptions();
        void loadReplay(face.time_ms / 1000);
      };
      container.append(seek);
    }
    const pitch = info.pitch;
    lineText(container, '声音语调', pitch?.median_f0_hz
      ? [
        {rising:'句末上扬',falling:'句末下降',level:'句末平稳'}[pitch.pitch_trend],
        `基频 ${pitch.median_f0_hz} Hz`, `音高跨度 ${pitch.pitch_range_st} 半音`,
        pitch.relative_to_speaker_st !== undefined ? `较本人基线 ${pitch.relative_to_speaker_st >= 0 ? '+' : ''}${pitch.relative_to_speaker_st} 半音` : '',
      ].filter(Boolean).join(' · ')
      : '有声信息不足');
    if (info.chars_per_second !== undefined) {
      const pace = {faster:'较常速偏快',slower:'较常速偏慢',typical:'接近常速'}[info.pace];
      lineText(container, '语速估计', [`${info.chars_per_second} 字/秒`, pace].filter(Boolean).join(' · '));
    }
    if (info.tone) lineText(container, '模型推测语气', `${[info.tone.tone, info.tone.strength].filter(Boolean).join(' · ')}（未核实）`);
  }
  lineText(container, '用语特征', info.wording?.join(' · '));
  return true;
}
function ruleText(checks) {
  const labels = [checks.question ? '问句' : '陈述'];
  if (checks.style) labels.push({honorific:'尊敬语', polite:'敬体', plain:'常体', none:'无谓语'}[checks.style]);
  if (checks.uses_nin) labels.push('使用「您」');
  if (checks.simplified_leak) labels.push('简体混入');
  if (checks.emotion_aside) labels.push('括号情绪');
  if (checks.tone_punctuation_warning) labels.push('标点待查');
  return labels.join(' · ');
}
function openSubTab(subtab) {
  currentSubTab = subtab;
  for (const key of ['compare', 'analysis', 'export']) {
    $(`subpanel-${key}`).hidden = key !== subtab;
    const btn = $(`tab-btn-${key}`);
    if (btn) btn.setAttribute('aria-selected', String(key === subtab));
  }
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

/* ===================== EXPERIMENT: VISUAL STORYBOARD ===================== */
function showExperiment(data) {
  if (!data?.rows.length) return;
  const strata = {
    main:'主表（剔除画面日文字幕）',
    chinese_onscreen:'画面可能有中文',
    no_chinese_onscreen:'画面未检出中文',
    chinese_question_onscreen:'画面中文含问号',
    japanese_onscreen:'画面有日文',
  };
  const metricLabels = {
    recall:'问句识别率', style:'文体一致率', comet:'综合语义 COMET', chrf:'字词匹配 chrF',
    f1:'问句 F1', false_positive:'问句误判率', Q5:'Q5 纯语调问句', Q2:'Q2 语气词问句',
    Q4:'Q4 疑问代词问句', Q3:'Q3 正反问句', Q1:'Q1 附加问句',
    kappa:'文体一致 κ', plain_baseline:'常体占比', mismatch_follow:'错配跟随率',
    simplified_leak:'简体字混入率', fallback:'回退率',
  };
  const metricExplain = {
    recall: '把原文末尾的问号去掉后，看模型仅凭文字 vs 加上声音/画面，能否准确翻译出日语问句。数值越高越好；错配组应回落。',
    style: '衡量模型译出的日语敬体/常体风格，与人工参考译文是否一致。数值越高越好。',
    comet: '神经网络语义翻译质量评分（0–1），综合衡量忠实度与自然度。本实验中「文字 + 真实声音」表现最佳。',
    chrf: '字符级 n-gram 匹配分（百分制），越高说明译文用词与人工参考越接近。',
    Q5: '最考验语音语调的问句：中文里没有任何「吗/呢/什么」疑问词，完全靠句末升调判断是不是问句。',
    false_positive: '把陈述句误译为问句的比例，越低越好。',
  };
  const quickMetrics = ['recall', 'style', 'comet', 'chrf'];
  const lowerIsBetter = new Set(['false_positive', 'fallback', 'simplified_leak']);
  const proportionMetrics = new Set(['recall', 'false_positive', 'f1', 'style', 'plain_baseline', 'fallback', 'simplified_leak', 'Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'mismatch_follow']);
  const condMeta = {
    T_neutral: ['仅看文字', '去掉问号的纯文本基线', 'base'],
    'T+A': ['文字 + 真实声音', '接入原句门控语音', 'multi'],
    'T+V': ['文字 + 真实画面', '接入无声视频帧', 'multi'],
    'T+V+A': ['文字 + 声音 + 画面', '完整音画交错输入', 'multi'],
    'T+A_shuf': ['文字 + 错配声音', '换用其他视频声音（反证）', 'shuf'],
    'T+V_shuf': ['文字 + 错配画面', '换用其他视频画面（反证）', 'shuf'],
    T_asr: ['原始语音转写', '保留原句问号，未经人工去标点', 'ref'],
  };

  const findVal = (stratumKey, cond, metricKey) => {
    const r = data.rows.find(row => row.stratum === stratumKey && row.condition === cond && row.metric === metricKey);
    return r ? Number(r.value) : null;
  };
  const formatVal = (m, raw) => {
    const n = Number(raw);
    if (!Number.isFinite(n)) return raw;
    if (proportionMetrics.has(m)) return `${(n * 100).toFixed(1)}%`;
    if (m === 'chrf') return n.toFixed(2);
    return n.toFixed(4);
  };
  const formatDelta = (m, val, base) => {
    if (!Number.isFinite(val) || !Number.isFinite(base)) return null;
    const diff = val - base;
    const sign = diff > 0 ? '+' : '';
    const txt = proportionMetrics.has(m) ? `${sign}${(diff * 100).toFixed(1)}%`
      : m === 'chrf' ? `${sign}${diff.toFixed(2)}`
      : `${sign}${diff.toFixed(4)}`;
    const good = lowerIsBetter.has(m) ? diff < -1e-6 : diff > 1e-6;
    const bad = lowerIsBetter.has(m) ? diff > 1e-6 : diff < -1e-6;
    return {txt, cls: good ? 'pos' : bad ? 'neg' : 'neutral'};
  };

  // Top 3 Plain-Language Takeaways
  const recBase = findVal('main', 'T_neutral', 'recall');
  const recA = findVal('main', 'T+A', 'recall');
  const recV = findVal('main', 'T+V', 'recall');
  const recVA = findVal('main', 'T+V+A', 'recall');
  const recShufA = findVal('main', 'T+A_shuf', 'recall');
  const cometBase = findVal('main', 'T_neutral', 'comet');
  const cometA = findVal('main', 'T+A', 'comet');
  const chrfA = findVal('main', 'T+A', 'chrf');
  const mainLines = data.rows.find(row => row.stratum === 'main' && row.metric === 'chrf')?.n || '656';
  const bestRec = Math.max(recA ?? 0, recV ?? 0, recVA ?? 0);

  $('experiment-meta').textContent = `基于 WMT26 中→日正式评测集：共 ${data.count} 段真实语音视频（主表 ${mainLines} 句）、7 组对照条件、0 次回退。`;

  const takeaways = [
    [
      '结论 1 · 音画显著提升问句识别',
      `${(recBase * 100).toFixed(1)}% → ${(bestRec * 100).toFixed(1)}%`,
      `去掉问号后，加入声音（${(recA * 100).toFixed(1)}%）或画面（${(recV * 100).toFixed(1)}%）最高提升 +${((bestRec - recBase) * 100).toFixed(1)} 个百分点。`,
      false,
    ],
    [
      '结论 2 · 错配对照证实并非巧合',
      `回落至 ${(recShufA * 100).toFixed(1)}%`,
      '故意把其他视频的声音/画面错配给模型后，得分立刻跌破纯文本基线，证明收益来自真实音画线索。',
      false,
    ],
    [
      '结论 3 · 综合质量与诚实权衡',
      `文字+声音最优（COMET ${cometA?.toFixed(4) ?? '—'}）`,
      `单加声音高于纯文本基线（${cometBase?.toFixed(4) ?? '—'}，chrF ${chrfA?.toFixed(2) ?? '—'}）；音画全加时存在无关画面干扰，留待后续门控优化。`,
      true,
    ],
  ];
  $('experiment-kpis').replaceChildren(...takeaways.map(([label, big, desc, isWarn]) => {
    const card = element('div', null, isWarn ? 'takeaway warn' : 'takeaway');
    card.append(element('small', label), element('strong', big), element('p', desc));
    return card;
  }));

  const stratum = $('experiment-stratum'), metric = $('experiment-metric'), pillHost = $('experiment-metric-pills');
  stratum.replaceChildren(...Object.keys(strata).filter(key => data.rows.some(row => row.stratum === key)).map(key => option(key, strata[key])));

  const render = () => {
    const m = metric.value;
    const filtered = data.rows.filter(row => row.stratum === stratum.value && row.metric === m);
    const byCond = Object.fromEntries(filtered.map(r => [r.condition, r]));
    const baseRow = byCond['T_neutral'];
    const baseNum = baseRow ? Number(baseRow.value) : null;

    // Update quick pills active state
    pillHost.querySelectorAll('button').forEach(btn => {
      btn.setAttribute('aria-pressed', String(btn.dataset.metric === m));
    });
    $('experiment-metric-explain').textContent = metricExplain[m] || `当前展示「${strata[stratum.value] || stratum.value}」分组下的「${metricLabels[m] || m}」对比（误差线为 95% 置信区间）。`;

    // Scale domain for crisp visual contrast (zoomed slightly for COMET/chrF, 0..100% for proportions)
    const vals = filtered.map(r => Number(r.value)).filter(Number.isFinite);
    const cis = filtered.flatMap(r => [Number(r.ci_low), Number(r.ci_high)]).filter(Number.isFinite);
    const allNums = [...vals, ...cis];
    let minScale = 0, maxScale = 1;
    if (m === 'comet') {
      minScale = Math.max(0, Math.floor((Math.min(...allNums) - 0.03) * 20) / 20);
      maxScale = Math.min(1, Math.ceil((Math.max(...allNums) + 0.02) * 20) / 20);
    } else if (m === 'chrf') {
      minScale = Math.max(0, Math.floor((Math.min(...allNums) - 3) / 5) * 5);
      maxScale = Math.ceil((Math.max(...allNums) + 3) / 5) * 5;
    } else if (proportionMetrics.has(m)) {
      minScale = 0;
      maxScale = Math.max(0.2, Math.min(1, Math.ceil((Math.max(...allNums) + 0.08) * 10) / 10));
    } else {
      minScale = Math.min(0, ...allNums);
      maxScale = Math.max(1, ...allNums);
    }
    const pctPos = v => Math.max(0, Math.min(100, ((v - minScale) / Math.max(1e-6, maxScale - minScale)) * 100));

    // Find best among experimental conditions (excluding T_asr reference)
    const expNums = filtered.filter(r => r.condition !== 'T_asr').map(r => Number(r.value)).filter(Number.isFinite);
    const bestVal = expNums.length ? (lowerIsBetter.has(m) ? Math.min(...expNums) : Math.max(...expNums)) : null;

    // Group rows semantically so viewers immediately grasp the experiment logic!
    const groups = [
      ['基线对照', ['T_neutral']],
      ['真实多模态输入（验证音画是否有帮助）', ['T+A', 'T+V', 'T+V+A']],
      ['错配反事实对照（换用其他视频的假音画，证伪干扰）', ['T+A_shuf', 'T+V_shuf']],
      ['原始语音转写对照（保留问号标点，含语音识别误差）', ['T_asr']],
    ];

    const chart = $('experiment-visual-chart');
    chart.replaceChildren();
    for (const [gTitle, condKeys] of groups) {
      const rowsInGroup = condKeys.map(k => byCond[k]).filter(Boolean);
      if (!rowsInGroup.length) continue;
      const gWrap = element('div');
      const gHead = element('div', null, 'chart-group-title');
      gHead.append(
        element('span', gTitle),
        element('span', gTitle.startsWith('基线') ? `虚线 = 纯文本基线 · 黑线 = 95% 置信区间 · 刻度 ${formatVal(m, minScale)} – ${formatVal(m, maxScale)}` : ''),
      );
      gWrap.append(gHead);
      const list = element('div', null, 'chart-rows');

      for (const row of rowsInGroup) {
        const val = Number(row.value);
        const [title, desc, kind] = condMeta[row.condition] || [conditions[row.condition] || row.condition, '', 'multi'];
        const isBest = bestVal != null && val === bestVal && row.condition !== 'T_asr' && row.condition !== 'T_neutral';
        const rNode = element('div', null, isBest ? 'chart-row best' : 'chart-row');

        const lbl = element('div', null, 'chart-label');
        lbl.append(element('strong', title + (isBest ? ' ★' : '')), element('small', desc));

        const barArea = element('div', null, 'chart-bar-area');
        barArea.title = row.ci_low ? `${formatVal(m, row.value)}（95% CI: ${formatVal(m, row.ci_low)} – ${formatVal(m, row.ci_high)}，n=${row.n}）` : `${formatVal(m, row.value)}（n=${row.n}）`;
        const bar = element('div', null, `chart-bar ${kind}`);
        bar.style.width = `${Math.max(1.5, pctPos(val))}%`;
        barArea.append(bar);

        if (baseNum != null && row.condition !== 'T_neutral') {
          const baseLine = element('div', null, 'chart-base-line');
          baseLine.style.left = `${pctPos(baseNum)}%`;
          barArea.append(baseLine);
        }
        if (row.ci_low && row.ci_high) {
          const lo = pctPos(Number(row.ci_low));
          const hi = pctPos(Number(row.ci_high));
          if (hi > lo) {
            const ci = element('div', null, 'chart-ci');
            ci.style.left = `${lo}%`;
            ci.style.width = `${hi - lo}%`;
            barArea.append(ci);
          }
        }

        const valBox = element('div', null, 'chart-val');
        valBox.append(element('strong', formatVal(m, row.value)));
        if (row.condition === 'T_neutral') {
          valBox.append(element('span', '基线', 'delta neutral'));
        } else if (row.condition === 'T_asr') {
          valBox.append(element('span', '含原标点', 'delta neutral'));
        } else if (baseNum != null) {
          const d = formatDelta(m, val, baseNum);
          if (d) valBox.append(element('span', d.txt, `delta ${d.cls}`));
        }

        rNode.append(lbl, barArea, valBox);
        list.append(rNode);
      }
      gWrap.append(list);
      chart.append(gWrap);
    }

    // Raw table inside collapsible details
    const table = element('table');
    const head = table.createTHead().insertRow();
    ['条件', '样本数 n', '数值', '相对基线', '95% 置信区间'].forEach(text => head.append(element('th', text)));
    const body = table.createTBody();
    for (const row of filtered) {
      const tr = body.insertRow();
      const [title] = condMeta[row.condition] || [conditions[row.condition] || row.condition];
      const d = row.condition === 'T_neutral' ? '基线' : row.condition === 'T_asr' ? '含原标点' : (formatDelta(m, Number(row.value), baseNum)?.txt || '—');
      const ci = row.ci_low ? `${formatVal(m, row.ci_low)} – ${formatVal(m, row.ci_high)}` : '—';
      [title, row.n, formatVal(m, row.value), d, ci].forEach(text => { tr.insertCell().textContent = text; });
    }
    $('experiment-table').replaceChildren(table);
  };

  stratum.onchange = () => {
    const previous = metric.value;
    const keys = Object.keys(metricLabels).filter(key => data.rows.some(row => row.stratum === stratum.value && row.metric === key));
    metric.replaceChildren(...keys.map(key => option(key, metricLabels[key])));
    pillHost.replaceChildren(...quickMetrics.filter(k => keys.includes(k)).map(k => {
      const btn = element('button', metricLabels[k], 'metric-pill');
      btn.type = 'button';
      btn.dataset.metric = k;
      btn.onclick = () => { metric.value = k; render(); };
      return btn;
    }));
    if (keys.includes(previous)) metric.value = previous;
    render();
  };
  metric.onchange = render;
  stratum.onchange();

  $('experiment-figures').replaceChildren(...data.figures.map(figure => {
    const card = element('figure', null, 'figure-card');
    card.append(element('figcaption', figure.label));
    const image = element('img'); image.alt = figure.label; image.src = figure.path; image.loading = 'lazy';
    card.append(image);
    return card;
  }));
  document.querySelector('[data-tab="experiment"]').hidden = false;
}

/* ===================== REPLAY: CLEAN 3-TAB DETAIL VIEW ===================== */
function selectLine(row, seek = false) {
  selected = row;
  const node = $('selected-line'); node.replaceChildren();
  if (!row) {
    $('selected-id').textContent = '';
    node.append(element('p','当前会话没有句子。','empty'));
    return;
  }
  $('selected-id').textContent = row.sample_id?.split('-').at(-1) || row.line_id;
  const grid = element('div', null, 'detail-grid');

  const metaBox = element('div', null, 'detail-box');
  metaBox.append(element('h3', '人物 · 声学 · 语气与识别复核'));
  let hasMeta = false;

  if (selectedDub()?.lines.includes(row)) {
    if (row.asr_recheck) { asrReview(metaBox, row.asr_recheck); hasMeta = true; }
    if (row.tone) { lineText(metaBox, '原声语气', `${[row.tone, row.strength].filter(Boolean).join(' · ')}（未核实）`); hasMeta = true; }
    if (row.object_evidence) { lineText(metaBox, '物体线索（未核实）', row.object_evidence); hasMeta = true; }
    if (row.voice_reference) {
      const r = row.voice_reference;
      lineText(metaBox, '音色参考', `${r.line_id} · ${((r.end_ms - r.start_ms) / 1000).toFixed(1)} 秒原声`);
      hasMeta = true;
    }
    if (row.intonation) {
      const shift = row.intonation.max_shift_semitones != null ? `（最大迁移 ${row.intonation.max_shift_semitones} 半音）` : '';
      lineText(metaBox, '句级语调迁移', row.intonation.status === 'applied' ? `已跟随原声升降走势${shift}` : '沿用基础配音语调');
      hasMeta = true;
    }
    if (row.timing) {
      const t = row.timing;
      const pct = Math.round((t.coverage ?? 0) * 100);
      lineText(
        metaBox,
        '音画时长对齐',
        `原声 ${(t.source_dur_ms / 1000).toFixed(2)} s → 配音 ${(t.aligned_dur_ms / 1000).toFixed(2)} s（综合语速 ${t.effective_speed}× · 贴合 ${pct}%）`,
      );
      hasMeta = true;
    }
    const textBox = element('div', null, 'detail-box');
    textBox.append(element('h3', '配音对照'));
    lineText(textBox, '原句台词', row.text_asr);
    lineText(textBox, '字幕定稿', row.multimodal);
    if (row.dub_script && row.dub_script !== row.multimodal) lineText(textBox, '精简配音稿', row.dub_script);
    if (row.tts_text) lineText(textBox, '合成读音', row.tts_text);
    if (hasMeta) grid.append(metaBox);
    grid.append(textBox);
    node.append(grid);
    renderTable();
    return;
  }

  if (showSpeakerDetail(metaBox, speakerRow(active, row))) hasMeta = true;

  if (active.kind === 'realtime') {
    if (row.asr_recheck) { asrReview(metaBox, row.asr_recheck); hasMeta = true; }
    if (row.joint_with) { lineText(metaBox, '跨句修订前', row.before_joint); hasMeta = true; }
    if (row.observation) { observation(metaBox, row.observation); hasMeta = true; }
  } else {
    if (row.asr_recheck) { asrReview(metaBox, row.asr_recheck); hasMeta = true; }
    if (active.pair) {
      lineText(metaBox, '预期读法', active.pair.expected_reading);
      if (!Object.keys(active.pair.reviews || {}).length || active.pair.review !== '待人工核对') {
        lineText(metaBox, '总判定', active.pair.review);
      }
      hasMeta = true;
    }
    if (row.asr_warning) { metaBox.append(element('p','识别可信度待核查','star')); hasMeta = true; }
    for (const condition of active.conditions) {
      const output = row.outputs[condition];
      if (output.observation) { observation(metaBox, output.observation, `${conditions[condition] || condition} · `); hasMeta = true; }
    }
    if (seek && !selectedDub()) {
      const button = $('full-video'); button.hidden = false;
      replayClip = row;
      updateReplayCaptions();
      void loadReplay(0, true);
    }
  }

  if (!hasMeta) {
    metaBox.append(element('p', '该句暂无人物声纹或模型语气记录。可点击上方「分析人物和语气」补充分析。', 'subtle'));
  }
  grid.append(metaBox);

  // Compact single-line summary card for this sentence's key contrast
  const transBox = element('div', null, 'detail-box');
  transBox.append(element('h3', '本句原文与译文要点'));
  if (active.kind === 'realtime') {
    lineText(transBox, '识别原文', row.text_asr);
    lineText(transBox, '快速译文', row.quick);
    lineText(transBox, '音画修订', row.skipped ? '已跳过' : row.multimodal, row.changed);
  } else {
    lineText(transBox, active.transcript_kind === 'controlled' ? '固定台词' : '识别原文', row.text.text_asr);
    for (const condition of active.conditions) {
      const label = conditions[condition] || condition;
      lineText(transBox, label, row.outputs[condition].text, row.compare?.[condition]?.different);
    }
  }
  grid.append(transBox);
  node.append(grid);

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
  const columns = realtime ? ['时间','识别原文','快速译文（纯文本）','音画修订（多模态）'] : ['时间','识别原文', ...active.conditions.map(c => conditions[c] || c)];
  columns.forEach((label, idx) => {
    const th = element('th', label);
    if ((realtime && idx === 3) || (!realtime && active.conditions[idx - 2] === 'T+V+A')) th.className = 'col-highlight';
    head.append(th);
  });
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
      const revCell = tr.insertCell();
      revCell.className = 'col-highlight';
      revCell.append(element('span', row.skipped ? '已跳过' : row.multimodal, row.changed ? 'changed' : ''));
      continue;
    }
    for (const condition of active.conditions) {
      const cell = tr.insertCell();
      if (condition === 'T+V+A') cell.className = 'col-highlight';
      const diff = row.compare?.[condition];
      if (diff?.parts?.some(part => part.changed)) diff.parts.forEach(part => cell.append(element('span', part.text, part.changed ? 'changed' : '')));
      else cell.append(element('span', row.outputs[condition].text, diff?.different ? 'changed' : ''));
      cell.append(element('small', ruleText(row.checks[condition])));
    }
  }
  $('compare-table').replaceChildren(filtered.length ? table : element('p', '当前筛选下没有有差异的句子。','empty'));
  $('tab-btn-compare').textContent = realtime ? '两阶段译文对比' : '多条件译文对比';
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
function syncDubButtons() {
  const ver = $('replay-version').value;
  $('dub-switch').querySelectorAll('button[data-ver]').forEach(btn => {
    btn.setAttribute('aria-pressed', String(btn.dataset.ver === ver));
  });
}
function showDub() {
  const dub = active?.dub;
  const ready = dub?.status === 'complete';
  const busy = dub?.status === 'running';
  const prosody = dub?.prosody;
  $('dub-switch').hidden = !ready;
  $('dub-download').hidden = !ready;
  if (ready) {
    $('dub-download').href = dub.video;
    const dubLabel = dub.language === 'ja' ? '日语 · AI 配音' : '中文 · AI 配音';
    $('replay-version').options[0].textContent = dubLabel;
    $('dub-ver-btn').textContent = dubLabel;
    syncDubButtons();
  }
  $('dub-form').hidden = !dubbingAvailable || !active?.can_dub || ready || busy;
  const pill = $('dub-prosody-pill');
  if (pill) {
    pill.hidden = !(ready && prosody);
    if (ready && prosody) pill.textContent = `原声语调迁移 ${prosody.applied_lines}/${prosody.total_lines} 句`;
  }
  const timingPill = $('dub-timing-pill');
  if (timingPill) {
    const t = dub?.timing;
    timingPill.hidden = !(ready && t);
    if (ready && t) timingPill.textContent = `音画贴合 ${Math.round((t.median_coverage ?? 0) * 100)}%`;
  }
  const redubBtn = $('redub-btn');
  if (redubBtn) redubBtn.hidden = !(ready && dubbingAvailable && active?.can_dub && !busy);
  $('dub-status').hidden = !busy && dub?.status !== 'failed';
  $('dub-status').textContent = busy && dub.total
    ? `${dub.message}（${dub.completed}/${dub.total} 句）`
    : dub?.message || '正在准备配音';
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
      `已区分 ${analysis.speakers.length} 位说话人`,
      uncertain ? `${uncertain} 句待确认` : '',
      analysis.model_tone ? `模型语气 ${analysis.model_tone.labeled}/${analysis.model_tone.eligible} 句` : '',
      analysis.vision_status !== 'complete' ? '口型未完成' : '',
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
    if (row === selected) button.setAttribute('aria-current', 'true');
    const start = dub || realtime ? row.start_ms : row.utterance.t_start_abs_ms;
    const text = dub || realtime ? row.text_asr : row.text.text_asr;
    const translation = dub || realtime ? (condition === 'quick' && !dub ? row.quick : row.multimodal || row.quick) : row.outputs[condition]?.text;
    const spk = dub ? null : speakerRow(active, row);
    const label = speakerLabel(spk);

    const head = element('div', null, 'row-head');
    const meta = [time(start)];
    if (label) meta.push(label);
    if (spk?.face) meta.push(`画面${{left:'左侧',center:'中央',right:'右侧'}[spk.face.side]}`);
    head.append(element('time', meta.join(' · ')));
    if (!dub && ((realtime && row.changed) || (!realtime && row.compare?.[condition]?.different))) {
      head.append(element('span', '已修订', 'diff-mark'));
    }

    button.append(head, element('strong', translation || text), element('span', text, 'row-src'));
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
function syncSessionCards(id) {
  $('session-cards')?.querySelectorAll('.session-card').forEach(card => {
    card.setAttribute('aria-selected', String(card.dataset.runId === id));
  });
}
function selectRun(id) {
  const video = $('replay-video');
  const changed = active?.id !== id;
  if (changed || replayClip) {
    clearMedia(video);
    $('replay-buffer').hidden = $('retry-replay').hidden = true;
  }
  active = runs.find(run => run.id === id) || null;
  syncSessionCards(id);
  if ($('run-select').value !== id && active) $('run-select').value = id;
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
  $('replay-csv').hidden = !active?.latency_csv;
  const counterpart = active?.pair && runs.find(run =>
    run.pair?.batch === active.pair.batch && run.pair.pair_id === active.pair.pair_id && run.id !== active.id);
  const switchPair = $('switch-pair');
  switchPair.hidden = !counterpart;
  if (counterpart) {
    switchPair.textContent = `查看 ${counterpart.pair.variant} 版`;
    switchPair.onclick = () => { selectRun(counterpart.id); };
  }
  if (active) {
    $('replay-title').textContent = friendlyRunTitle(active);
    const actions = $('run-actions');
    if (actions) actions.hidden = !active.deletable;
    Object.keys(active.download).forEach(key => $('download-condition').append(option(key, conditions[key] || key)));
    $('download-condition').value = active.download['T+V+A'] ? 'T+V+A' : Object.keys(active.download)[0];
    const ts = shortRunTime(active.id);
    const parts = [directionText[active.direction], `${modes[active.mode]}模式`];
    if (ts) parts.push(ts);
    if (active.size_bytes) parts.push(formatBytes(active.size_bytes));
    if (active.asr_qwen?.status === 'complete') parts.push(`Qwen3-ASR 复听 ${active.asr_qwen.selected ?? active.asr_qwen.total}/${active.asr_qwen.total} 句，修正 ${active.asr_qwen.applied} 句`);
    $('replay-meta').textContent = parts.join(' · ');
    $('replay-meta').title = `${active.name} · ${active.model.model} · ${active.id}`;
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
    // Auto-select the most illuminating detail tab when switching sessions
    if (changed) {
      if (active.speaker_analysis?.status === 'complete' || active.asr_qwen?.status === 'complete') {
        openSubTab('analysis');
      } else {
        openSubTab('compare');
      }
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
function formatRunOption(run) {
  const ts = shortRunTime(run.id);
  return `${friendlyRunTitle(run)} · ${friendlyRunSubtitle(run)}${ts ? ` (${ts})` : ''}`;
}
function matchesHistory(run) {
  if (historyFilter === 'dub' && run.dub?.status !== 'complete') return false;
  if (historyFilter === 'zh2ja' && run.direction !== 'zh2ja') return false;
  if (historyFilter === 'ja2zh' && run.direction !== 'ja2zh') return false;
  if (!historyQuery) return true;
  const q = historyQuery.toLowerCase();
  if (friendlyRunTitle(run).toLowerCase().includes(q)) return true;
  if ((run.name || '').toLowerCase().includes(q) || (run.id || '').toLowerCase().includes(q)) return true;
  return (run.lines || []).some(row => {
    const src = row.text_asr || row.text?.text_asr || '';
    const tgt = row.multimodal || row.quick || Object.values(row.outputs || {}).map(o => o.text).join(' ');
    return src.toLowerCase().includes(q) || tgt.toLowerCase().includes(q);
  });
}
function renderSessionCards() {
  const cardsHost = $('session-cards');
  const emptyHint = $('history-empty');
  const visible = runs.filter(matchesHistory);
  $('history-toolbar').hidden = runs.length === 0;
  // Update filter button counts
  const counts = {
    all: runs.length,
    dub: runs.filter(r => r.dub?.status === 'complete').length,
    zh2ja: runs.filter(r => r.direction === 'zh2ja').length,
    ja2zh: runs.filter(r => r.direction === 'ja2zh').length,
  };
  const labels = {all: '全部', dub: '含 AI 配音', zh2ja: '中 → 日', ja2zh: '日 → 中'};
  $('history-filters')?.querySelectorAll('button[data-filter]').forEach(btn => {
    const k = btn.dataset.filter;
    btn.textContent = `${labels[k]} (${counts[k]})`;
    btn.setAttribute('aria-pressed', String(historyFilter === k));
  });
  cardsHost.hidden = visible.length === 0;
  if (emptyHint) emptyHint.hidden = visible.length > 0 || runs.length === 0;
  cardsHost.replaceChildren(...visible.map(run => {
    const btn = element('div', null, 'session-card');
    btn.tabIndex = 0;
    btn.setAttribute('role', 'option');
    btn.dataset.runId = run.id;
    btn.setAttribute('aria-selected', String(active?.id === run.id));
    const ts = shortRunTime(run.id);
    btn.title = `${friendlyRunTitle(run)}${ts ? ` (${ts})` : ''}`;
    btn.append(element('strong', friendlyRunTitle(run)), element('span', friendlyRunSubtitle(run)));
    btn.onclick = () => selectRun(run.id);
    btn.onkeydown = event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectRun(run.id); }
    };
    if (run.deletable) {
      const del = element('button', '×', 'card-del');
      del.type = 'button';
      del.title = '删除此会话';
      del.setAttribute('aria-label', `删除 ${friendlyRunTitle(run)}`);
      del.onclick = event => {
        event.stopPropagation();
        void deleteRunById(run);
      };
      btn.append(del);
    }
    return btn;
  }));
}
async function refreshStorage() {
  const host = $('storage-tools');
  if (!host || config.runs !== null) return;
  try {
    const info = await fetch('/api/storage', {cache: 'no-store'}).then(r => r.ok ? r.json() : null);
    if (!info) return;
    host.hidden = false;
    const parts = [`${info.runs_count} 个会话`, `共 ${formatBytes(info.total_bytes)}`];
    $('storage-info').textContent = parts.join(' · ');
    const btn = $('cleanup-runs');
    const canClean = info.reclaimable_runs > 0 || info.reclaimable_bytes >= 256 * 1024;
    btn.hidden = !canClean;
    if (canClean) {
      btn.textContent = info.reclaimable_bytes >= 1024 * 1024
        ? `一键减容（可省 ${formatBytes(info.reclaimable_bytes)}）`
        : `清理无效记录 (${info.reclaimable_runs})`;
    }
  } catch { /* ignore transient offline error */ }
}
async function deleteRunById(run) {
  if (!run?.deletable) return;
  const sizeTxt = run.size_bytes ? `（将释放 ${formatBytes(run.size_bytes)}）` : '';
  if (!window.confirm(`确定删除会话「${friendlyRunTitle(run)}」${sizeTxt}？删除后无法恢复。`)) return;
  const response = await fetch(`/api/runs/${run.id}`, {method: 'DELETE'});
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    window.alert(detail.detail || '删除失败，请稍后重试。');
    return;
  }
  const next = runs.find(item => item.id !== run.id)?.id;
  await refreshRuns(active?.id === run.id ? next : active?.id);
}
async function renameActiveRun() {
  if (!active?.deletable) return;
  const current = friendlyRunTitle(active);
  const input = window.prompt('输入新的会话名称（留空则恢复默认名称）：', current);
  if (input === null) return;
  const response = await fetch(`/api/runs/${active.id}`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title: input.trim()}),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    window.alert(detail.detail || '重命名失败。');
    return;
  }
  await refreshRuns(active.id);
}
async function refreshRuns(preferred) {
  runs = config.runs || await fetch('/api/runs').then(response => response.json());
  const select = $('run-select');
  const previous = preferred || select.value;

  select.replaceChildren(...runs.map(run => option(run.id, formatRunOption(run))));
  const target = runs.some(run => run.id === previous) ? previous : runs[0]?.id;
  if (target) {
    select.value = target;
    selectRun(target);
  } else {
    selectRun(null);
  }
  renderSessionCards();
  void refreshStorage();
}

/* ===================== LIVE TAB ENGINE (UNCHANGED FLOW) ===================== */
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
function finishJobProgress(message, failed = false, detail = failed ? '可检查错误后重新提交' : '可在结果回放查看完整对比') {
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
  const head = element('div', null, 'sentence-head');
  const meta = [`${time(row.start_ms)} – ${time(row.end_ms)}`];
  if (row.observation?.tone) meta.push(row.observation.tone);
  head.append(element('time', meta.join(' · ')));
  if (row.changed) head.append(element('span', '音画已修订', 'diff-mark'));
  node.append(head);
  if (row.multimodal || row.quick) node.append(element('strong', row.multimodal || row.quick, 'translation'));
  node.append(element('p', row.text_asr, 'original'));
  asrReview(node, row.asr_recheck);
  if (row.joint_with) lineText(node, '跨句修订前', row.before_joint);
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
  const makeCard = (title, values, isStage = false) => {
    const card = element('div', null, isStage ? 'metric stage' : 'metric');
    const value = values?.median_ms == null ? '—' : `${(values.median_ms / 1000).toFixed(2)} s`;
    card.append(element('small', title), element('strong', value));
    if (values?.p90_ms != null) card.append(element('small', `P90 ${(values.p90_ms / 1000).toFixed(2)} s`));
    return card;
  };
  const primary = element('div', null, 'metrics-primary');
  primary.append(
    makeCard('原文延迟', summary.asr),
    makeCard('快速译文', summary.quick),
    makeCard('音画修订', summary.multimodal),
  );
  const skip = element('div', null, 'metric');
  skip.append(element('small','音画跳过率'), element('strong', summary.skip_rate == null ? '—' : `${(summary.skip_rate * 100).toFixed(1)}%`), element('small', `共 ${summary.utterances ?? 0} 句`));
  primary.append(skip);

  const stages = [['切句等待','vad_wait'],['任务排队','queue_wait'],['语音识别','asr_inference'],
    ['快译调用','quick_translation'],['媒体观察','observation'],['音画修订','multimodal_translation']];
  const stageCards = stages.filter(([,key]) => summary.stages?.[key]?.n).map(([title,key]) => makeCard(title, summary.stages[key], true));
  const nodes = [primary];
  if (stageCards.length) {
    const row = element('div', null, 'metrics-stages');
    row.append(...stageCards);
    nodes.push(row);
  }
  list.replaceChildren(...nodes);
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
        case 'revised_sentence':
          renderLive(event);
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
            $('open-result').onclick = () => { selectRun(event.run_id); openTab('replay'); };
            const video = $('live-video');
            if (active.video && workflow !== 'offline') {
              if (workflow === 'webcam' || workflow === 'fast') {
                video.muted = false;
                $('live-audio').textContent = workflow === 'fast'
                  ? '原声'
                  : (active.replay_quality === 'continuous' ? '麦克风原声' : '麦克风原声 · 抽帧回放');
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
  $('fast-asr-note').hidden = !qwenAsrAvailable || source === 'webcam' || form.elements.workflow.value !== 'fast';
  $('qwen-asr-setting').hidden = form.elements.qwen_recheck.disabled = !qwen;
  $('dub-request').hidden = form.elements.dub.disabled = !dubbingAvailable || source === 'sample';
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
  $('live-lines').replaceChildren(element('p','等待识别第一句…','empty')); liveRows = new Map();
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
  document.querySelectorAll('[data-subtab]').forEach(button => {
    button.onclick = () => openSubTab(button.dataset.subtab);
  });
  $('dub-switch').querySelectorAll('button[data-ver]').forEach(button => {
    button.onclick = () => {
      const target = button.dataset.ver;
      if ($('replay-version').value === target) return;
      $('replay-version').value = target;
      $('replay-version').onchange();
    };
  });

  showExperiment(config.experiment);
  $('run-select').onchange = () => selectRun($('run-select').value);
  $('history-filters')?.querySelectorAll('button[data-filter]').forEach(btn => {
    btn.onclick = () => {
      historyFilter = btn.dataset.filter;
      renderSessionCards();
      const visible = runs.filter(matchesHistory);
      if (visible.length && !visible.some(r => r.id === active?.id)) selectRun(visible[0].id);
    };
  });
  $('history-search').oninput = event => {
    historyQuery = event.target.value.trim();
    renderSessionCards();
  };
  $('rename-run').onclick = () => { void renameActiveRun(); };
  $('delete-run').onclick = () => { void deleteRunById(active); };
  $('cleanup-runs').onclick = async () => {
    const btn = $('cleanup-runs');
    btn.disabled = true;
    try {
      const result = await fetch('/api/runs/cleanup', {method: 'POST'}).then(r => r.json());
      await refreshRuns(active?.id);
      const freed = formatBytes(result.freed_bytes || 0);
      const msg = `清理完成：移除 ${result.removed_runs?.length || 0} 个无效目录，瘦身 ${result.slimmed_runs || 0} 个会话，释放 ${freed}。`;
      $('storage-info').textContent = msg;
      setTimeout(() => { void refreshStorage(); }, 3500);
    } finally {
      btn.disabled = false;
    }
  };
  $('redub-btn').onclick = async () => {
    if (!active) return;
    if (!window.confirm('将使用最新的双向语速对齐与自然底噪融合重新生成 AI 配音，确认继续？')) return;
    const id = active.id;
    const btn = $('redub-btn');
    btn.disabled = true;
    try {
      const form = new FormData();
      form.set('consent', '1');
      form.set('force', '1');
      const response = await fetch(`/api/runs/${id}/dub`, {method: 'POST', body: form});
      if (!response.ok) throw Error((await response.json()).detail || '无法重新启动配音。');
      await watchDub(id);
      void refreshStorage();
    } catch (error) {
      window.alert(error.message);
    } finally {
      btn.disabled = false;
    }
  };
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
