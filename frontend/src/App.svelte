<script>
  let profile = null;
  let username = "admin";
  let password = "123456";
  let loginError = "";
  let board = null;
  let selectedCrop = null;
  let buildMode = "per_crop";
  let queue = [];
  let queueTouched = false;
  let previewData = null;
  let previewError = "";
  let eventsOpen = false;
  let libraryFilter = "all";
  let openTrials = {};
  let trialDetails = {};
  let busy = false;
  let actionError = "";
  let notice = "";

  try {
    profile = JSON.parse(localStorage.getItem("germ.profile") || "null");
  } catch {
    profile = null;
  }

  $: selectedCropInfo =
    (board && board.crops.find((crop) => crop.crop_name === selectedCrop)) || null;
  $: selectedTrials =
    (board && board.trials.filter((trial) => trial.crop_name === selectedCrop)) || [];
  $: filteredLibrary = board
    ? board.library.filter((row) => libraryFilter === "all" || row.mode === libraryFilter)
    : [];
  $: buildModeLabel = buildMode === "aggregate" ? "通栏" : "分作物";

  async function request(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    const token = localStorage.getItem("germ.token") || "";
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(path, { ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "请求没有完成");
    return data;
  }

  function post(path, payload) {
    return request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async function reloadBoard() {
    board = await request("/api/board");
    if (selectedCrop === null && board.crops.length) selectedCrop = board.crops[0].crop_name;
    await refreshPreview();
  }

  async function refresh() {
    board = await request("/api/board");
    if (board.split && board.split.mode) buildMode = board.split.mode;
    if (selectedCrop === null && board.crops.length) selectedCrop = board.crops[0].crop_name;
    if (!queueTouched) {
      queue = board.crops.filter((crop) => crop.complete && !crop.landed).map((crop) => crop.crop_name);
    }
    await refreshPreview();
  }

  async function refreshPreview() {
    if (!board) return;
    previewError = "";
    try {
      previewData = await post("/api/library/preview", { mode: buildMode, crops: queue });
    } catch (error) {
      previewData = null;
      previewError = error.message;
    }
  }

  async function submitLogin() {
    loginError = "";
    try {
      const data = await post("/api/login", { username, password });
      localStorage.setItem("germ.token", data.access_token);
      profile = { username: data.username, role: data.role, title: data.title };
      localStorage.setItem("germ.profile", JSON.stringify(profile));
      await refresh();
    } catch (error) {
      loginError = error.message;
    }
  }

  function selectCrop(crop) {
    selectedCrop = crop.crop_name;
  }

  function toggleQueue(crop) {
    queueTouched = true;
    queue = queue.includes(crop.crop_name)
      ? queue.filter((name) => name !== crop.crop_name)
      : [...queue, crop.crop_name];
    refreshPreview();
  }

  function setBuildMode(mode) {
    buildMode = mode;
    refreshPreview();
  }

  async function setSplit(mode) {
    busy = true;
    actionError = "";
    notice = "";
    try {
      board = await post("/api/split", { mode });
      buildMode = board.split.mode;
      notice = `今日分口已定：${board.split_label}，今日只此一口。`;
      await refreshPreview();
    } catch (error) {
      actionError = error.message;
    } finally {
      busy = false;
    }
  }

  async function publishBuild() {
    busy = true;
    actionError = "";
    notice = "";
    try {
      board = await post("/api/library/publish", { mode: buildMode, crops: queue });
      notice = `已按「${board.split_label}」落库，今日作物库共 ${board.library.length} 行。`;
      queue = queue.filter((name) => {
        const crop = board.crops.find((item) => item.crop_name === name);
        return crop && !crop.landed;
      });
      await refreshPreview();
    } catch (error) {
      actionError = error.message;
      await reloadBoard();
    } finally {
      busy = false;
    }
  }

  async function publishTrial(trial) {
    busy = true;
    actionError = "";
    notice = "";
    try {
      trialDetails = { ...trialDetails, [trial.id]: await post(`/api/trials/${trial.id}/publish`, {}) };
      notice = `${trial.lot_code} 已按现行口径出数。`;
      await reloadBoard();
    } catch (error) {
      actionError = error.message;
    } finally {
      busy = false;
    }
  }

  async function toggleTrial(trial) {
    openTrials = { ...openTrials, [trial.id]: !openTrials[trial.id] };
    if (openTrials[trial.id] && !trialDetails[trial.id]) {
      trialDetails = { ...trialDetails, [trial.id]: await request(`/api/trials/${trial.id}`) };
    }
  }

  function logout() {
    localStorage.removeItem("germ.token");
    localStorage.removeItem("germ.profile");
    profile = null;
    board = null;
  }

  if (profile) {
    refresh().catch(() => logout());
  }
</script>

{#if !profile}
  <main class="paper gate">
    <p class="kicker">种子检验室</p>
    <h1>发芽口径核算台</h1>
    <p>
      现行口径：分母是播种粒数减去霉粒数，分子是发芽粒数。分母落库前先定今日分口：
      全室收成通栏推进作物库，或按作物分行落入作物库，今日只准定一种。
    </p>
    <form on:submit|preventDefault={submitLogin}>
      <label>用户<input type="text" bind:value={username} /></label>
      <label>口令<input type="password" bind:value={password} /></label>
      <button>打开记录</button>
      {#if loginError}<p class="warn">{loginError}</p>{/if}
    </form>
  </main>
{:else}
  <header class="bar">
    <div>
      <p class="kicker">口径核算 · {board ? board.day : ""}</p>
      <h1>发芽口径核算台</h1>
    </div>
    <div class="who">
      <span>{profile.title} {profile.username}</span>
      <button class="quiet" on:click={logout}>离开</button>
    </div>
  </header>
  {#if board}
    <main class="desk">
      <aside class="paper crops">
        <div class="panelhead">
          <h2>作物列表</h2>
          <span class="splitname">分口名 · {board.split_label}</span>
        </div>
        <div class="cropgrid crophead">
          <span>作物</span><span>现行回数</span><span>分口回数</span>
        </div>
        {#each board.crops as crop (crop.crop_name)}
          <button
            class="cropgrid croprow"
            class:on={selectedCrop === crop.crop_name}
            on:click={() => selectCrop(crop)}
          >
            <span class="cname">
              {crop.display_name}
              {#if !crop.complete}<em class="tag miss">未齐</em>{/if}
              {#if crop.landed}<em class="tag ok">已落</em>{/if}
            </span>
            <span class="num">{crop.current_count}</span>
            <span class="num">{crop.split_count}</span>
          </button>
        {/each}
        <p class="hint">现行回数与分口回数同排对读；分口名：{board.split_label}。</p>
      </aside>

      <section class="paper detail">
        {#if selectedCropInfo}
          <div class="panelhead">
            <h2>分母详情 · {selectedCropInfo.display_name}</h2>
            {#if !selectedCropInfo.complete}<span class="tag miss">未齐</span>{/if}
          </div>
          {#if selectedCropInfo.reasons.length}
            <p class="warn">{selectedCropInfo.reasons.join("；")}</p>
          {/if}
          {#each selectedTrials as trial (trial.id)}
            <div class="trial">
              <div class="trialhead">
                <strong>{trial.lot_code}</strong>
                <span>{trial.status}</span>
                <em>现行回数 {trial.current_count}</em>
              </div>
              {#if trial.denominator}
                <p class="denom">
                  现行分母 {trial.denominator.tested} · 发芽 {trial.denominator.sprouted} ·
                  霉粒 {trial.denominator.excluded_mold} · 率 {trial.denominator.rate_label}
                </p>
              {:else}
                <p class="warn">缺现行分母：{trial.denominator_error}</p>
              {/if}
              <div class="trialops">
                <button disabled={profile.role !== "writer" || busy} on:click={() => publishTrial(trial)}>
                  按现行口径出数
                </button>
                <button class="quiet" on:click={() => toggleTrial(trial)}>
                  {openTrials[trial.id] ? "合上" : "翻开"}床次与出数记录
                </button>
              </div>
              {#if openTrials[trial.id] && trialDetails[trial.id]}
                <table>
                  <thead>
                    <tr><th>床</th><th>播种</th><th>发芽</th><th>霉粒</th></tr>
                  </thead>
                  <tbody>
                    {#each trialDetails[trial.id].beds as bed}
                      <tr><td>{bed.bed_no}</td><td>{bed.sown}</td><td>{bed.sprouted}</td><td>{bed.moldy}</td></tr>
                    {/each}
                  </tbody>
                </table>
                {#each trialDetails[trial.id].publications as row}
                  <section class="edition" class:old={!row.current}>
                    <strong>{row.current ? "现行口径" : "旧口径"} {row.rate_label}</strong>
                    <span>分母 {row.tested} · 剔除霉粒 {row.excluded_mold} · 发芽 {row.sprouted}</span>
                    <em>{row.note} · {row.published_by} · {row.published_at}</em>
                  </section>
                {/each}
              {/if}
            </div>
          {/each}
          <div class="events">
            <button class="quiet" on:click={() => (eventsOpen = !eventsOpen)}>
              {eventsOpen ? "合上" : "翻开"}分口走偏与争用（{board.events.length}）
            </button>
            {#if eventsOpen}
              {#if !board.events.length}<p class="hint">今日没有走偏或争用。</p>{/if}
              {#each board.events as event (event.id)}
                <p class="event">
                  <b>{event.kind_label}</b> · {event.actor} · {event.created_at}<br />{event.detail}
                </p>
              {/each}
            {/if}
          </div>
        {:else}
          <p class="hint">作物列表是空的。</p>
        {/if}
      </section>

      <section class="paper preview">
        <div class="panelhead">
          <h2>预看</h2>
          <span class="hint">只看不落库</span>
        </div>
        {#if previewError}
          <p class="warn">{previewError}</p>
        {:else if previewData}
          <p class="pvline">构建走法 <b>{previewData.mode_label}</b> ｜ 今日分口 <b>{previewData.split_label}</b></p>
          {#if !previewData.split_mode}
            <p class="warn">今日分口未定，出数前请先在分口策略定口。</p>
          {/if}
          {#if previewData.drift}
            <p class="warn">
              分口落法偏了：今日分口是「{previewData.split_label}」，构建走的是「{previewData.mode_label}」，提交会被截住。
            </p>
          {/if}
          {#if previewData.blocked}
            <p class="warn">通栏整笔截住：</p>
            <ul>
              {#each previewData.blocked_reasons as reason}<li>{reason}</li>{/each}
            </ul>
          {/if}
          {#if previewData.rows.length}
            <table>
              <thead>
                <tr><th>落库行</th><th>分母</th><th>发芽</th><th>霉粒</th><th>率</th></tr>
              </thead>
              <tbody>
                {#each previewData.rows as row}
                  <tr>
                    <td>
                      {row.row_key}
                      {#if row.members.length > 1}<em class="hint">（覆盖 {row.members.join("、")}）</em>{/if}
                    </td>
                    <td>{row.tested}</td>
                    <td>{row.sprouted}</td>
                    <td>{row.excluded_mold}</td>
                    <td>{row.rate_label}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {:else if !previewData.blocked}
            <p class="hint">没有可落库的行（混口队列空，或作物未齐）。</p>
          {/if}
          {#if previewData.incomplete.length}
            <p class="pvsub">未齐作物（不准出数）：</p>
            <ul>
              {#each previewData.incomplete as crop}
                <li>{crop.display_name}：{crop.reasons.join("；")}</li>
              {/each}
            </ul>
          {/if}
          {#if previewData.conflicts.length}
            <p class="warn">争用预警：{previewData.conflicts.join("、")} 今日已落库，再提交只保留先手。</p>
          {/if}
        {/if}
      </section>

      <aside class="rightcol">
        <section class="paper">
          <h2>分口策略</h2>
          {#if board.split}
            <p>今日分口：<b>{board.split.label}</b></p>
            <p class="hint">
              {board.split.decided_by} 定于 {board.split.decided_at}。今日只此一口，构建走偏会被截住。
            </p>
          {:else}
            <p>今日分口未定，只准定一种。</p>
            {#if profile.role === "writer"}
              <div class="splitbtns">
                <button disabled={busy} on:click={() => setSplit("aggregate")}>定为通栏</button>
                <button disabled={busy} on:click={() => setSplit("per_crop")}>定为分作物</button>
              </div>
              <p class="hint">通栏：全室收成通栏推进作物库；分作物：按作物分行落入作物库。</p>
            {:else}
              <p class="hint">见证员不能定口。</p>
            {/if}
          {/if}
        </section>

        <section class="paper">
          <h2>混口队列</h2>
          <div class="modepick">
            <label>
              <input
                type="radio"
                name="buildmode"
                checked={buildMode === "aggregate"}
                on:change={() => setBuildMode("aggregate")}
              />
              通栏推进作物库
            </label>
            <label>
              <input
                type="radio"
                name="buildmode"
                checked={buildMode === "per_crop"}
                on:change={() => setBuildMode("per_crop")}
              />
              按作物分行落库
            </label>
          </div>
          {#if board.split && board.split.mode !== buildMode}
            <p class="warn">走偏预警：今日分口是「{board.split.label}」，当前构建走「{buildModeLabel}」。</p>
          {/if}
          {#each board.crops as crop (crop.crop_name)}
            <label class="qrow" class:off={crop.landed}>
              <input
                type="checkbox"
                disabled={crop.landed}
                checked={queue.includes(crop.crop_name)}
                on:change={() => toggleQueue(crop)}
              />
              <span>{crop.display_name}</span>
              {#if !crop.complete}<em class="tag miss">未齐</em>{/if}
              {#if crop.landed}<em class="tag ok">已落</em>{/if}
            </label>
          {/each}
          <button class="primary" disabled={profile.role !== "writer" || busy || !queue.length} on:click={publishBuild}>
            出数落库
          </button>
          {#if profile.role !== "writer"}<p class="hint">见证员只能核对，不能出数。</p>{/if}
          {#if actionError}<p class="warn">{actionError}</p>{/if}
          {#if notice}<p class="okline">{notice}</p>{/if}
        </section>

        <section class="paper">
          <div class="panelhead">
            <h2>今日落库</h2>
            <div class="filters">
              <button class:quiet={libraryFilter !== "all"} on:click={() => (libraryFilter = "all")}>全部</button>
              <button class:quiet={libraryFilter !== "aggregate"} on:click={() => (libraryFilter = "aggregate")}>通栏</button>
              <button class:quiet={libraryFilter !== "per_crop"} on:click={() => (libraryFilter = "per_crop")}>分作物</button>
            </div>
          </div>
          {#each filteredLibrary as row (row.id)}
            <div class="librow">
              <strong>{row.row_key === "全室" ? "全室（通栏）" : row.crop_name} · {row.rate_label}</strong>
              <span>
                分母 {row.tested} · 发芽 {row.sprouted} · 霉粒 {row.excluded_mold} · {row.mode_label}
                {#if row.mode === "aggregate"}· 覆盖 {row.members.join("、")}{/if}
              </span>
              <em>{row.published_by} · {row.published_at}</em>
            </div>
          {:else}
            <p class="hint">今日还没有落库行。</p>
          {/each}
        </section>
      </aside>
    </main>
  {/if}
{/if}

<style>
  .bar, .desk, .gate { width: min(1400px, calc(100% - 32px)); margin: 0 auto; }
  .bar { display: flex; justify-content: space-between; align-items: end; padding: 28px 0 8px; }
  .kicker { margin: 0 0 4px; letter-spacing: 0.16em; color: #b4332a; font-size: 13px; }
  h1, h2 { font-weight: 560; margin: 0; }
  h2 { font-size: 18px; }
  .who { display: flex; gap: 12px; align-items: center; }
  .desk {
    display: grid;
    grid-template-columns: 250px minmax(0, 1fr) 320px;
    gap: 16px;
    padding-bottom: 40px;
    align-items: start;
  }
  .rightcol { display: grid; gap: 16px; }
  .paper { background: rgba(255, 250, 242, 0.94); padding: 18px; box-shadow: 0 10px 30px rgba(44, 36, 22, 0.05); }
  .gate { margin-top: 40px; width: min(520px, calc(100% - 32px)); }
  form { display: grid; gap: 10px; }
  form label { display: grid; gap: 6px; }
  input[type="text"], input[type="password"] {
    border: 0;
    border-bottom: 1px solid #b4332a;
    background: transparent;
    padding: 8px 0;
    color: #2c2416;
  }
  button { background: #b4332a; color: #fffaf2; border: 0; padding: 8px 12px; }
  .quiet { background: transparent; color: #2c2416; border-bottom: 1px solid #2c2416; }
  .panelhead { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 8px; }
  .hint { color: #7a6a55; font-size: 13px; }
  .warn { color: #b4332a; }
  .okline { color: #2f6b3a; }

  .splitname { font-size: 13px; color: #b4332a; }
  .cropgrid { display: grid; grid-template-columns: 1fr 64px 64px; gap: 6px; align-items: center; }
  .crophead { font-size: 13px; color: #7a6a55; border-bottom: 1px solid rgba(180, 51, 42, 0.35); padding-bottom: 6px; }
  .crophead span:not(:first-child), .croprow .num { text-align: right; }
  .croprow {
    background: transparent;
    color: #2c2416;
    border-bottom: 1px solid rgba(180, 51, 42, 0.2);
    padding: 8px 0;
    text-align: left;
  }
  .croprow.on { background: #fffaf2; }
  .cname { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  .tag { font-style: normal; font-size: 12px; padding: 1px 6px; border: 1px solid currentColor; }
  .tag.miss { color: #b4332a; }
  .tag.ok { color: #2f6b3a; }

  .trial { border-top: 1px dashed rgba(180, 51, 42, 0.4); padding: 10px 0; display: grid; gap: 6px; }
  .trial:first-of-type { border-top: 0; }
  .trialhead { display: flex; gap: 10px; align-items: baseline; }
  .trialhead em, .edition em, .librow em { font-style: normal; color: #7a6a55; font-size: 13px; }
  .denom { margin: 0; }
  .trialops { display: flex; gap: 10px; }
  .events { margin-top: 12px; border-top: 1px solid rgba(180, 51, 42, 0.25); padding-top: 10px; }
  .event { margin: 8px 0 0; font-size: 14px; }

  table { width: 100%; border-collapse: collapse; margin: 8px 0 12px; }
  th, td { text-align: left; padding: 6px 4px; border-bottom: 1px solid rgba(180, 51, 42, 0.25); }
  .edition { display: grid; gap: 4px; padding: 10px 0; border-top: 1px dashed rgba(180, 51, 42, 0.4); }
  .old strong { color: #7a6a55; }

  .pvline b { color: #b4332a; }
  .pvsub { margin: 10px 0 4px; }
  ul { margin: 4px 0; padding-left: 20px; }

  .splitbtns { display: flex; gap: 10px; margin: 8px 0; }
  .modepick { display: grid; gap: 6px; margin-bottom: 8px; }
  .modepick label, .qrow { display: flex; gap: 8px; align-items: center; }
  .qrow { padding: 5px 0; border-bottom: 1px solid rgba(180, 51, 42, 0.15); }
  .qrow.off { color: #7a6a55; }
  .primary { width: 100%; margin-top: 10px; }
  .filters { display: flex; gap: 6px; }
  .filters button { padding: 4px 8px; font-size: 13px; }
  .librow { display: grid; gap: 2px; padding: 8px 0; border-top: 1px dashed rgba(180, 51, 42, 0.4); }

  @media (max-width: 1100px) {
    .desk { grid-template-columns: 220px minmax(0, 1fr); }
    .rightcol { grid-column: 1 / -1; grid-template-columns: repeat(3, 1fr); }
  }
  @media (max-width: 800px) {
    .desk, .rightcol { grid-template-columns: 1fr; }
  }
</style>
