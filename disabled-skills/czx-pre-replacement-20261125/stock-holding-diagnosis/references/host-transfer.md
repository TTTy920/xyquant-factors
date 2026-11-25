# OpenCode 内存交接

只在 OpenCode 与下载器位于**同一台机器**，并支持 Code Mode 的 MCP + 回环 HTTP 时使用。已有可信 stdin 的宿主优先使用 `save`。`receive` 仅绑定 `127.0.0.1`，用随机端口和单次本地 nonce，按期自动退出；不能部署为网络服务、代理到远端或在浏览器里运行。

1. 确认任务 ready 后启动下方命令。读启动行的 `port`、`local_nonce`，不是 XYQuant 令牌。每个任务启动一个接收器；`--kind` 必须选对应数据类型。

   ```text
   python <本Skill>/scripts/mcp_transfer.py receive --kind <factors或holdings> --job <任务ID> --project <项目绝对路径>
   ```

2. **在同一次 Code Mode 调用里**取得已就绪任务，并把它作为 JSON 直接 POST 到此次启动行给出的本机地址。不单独返回工具结果，不打印 `job`、下载头或异常详情，不把它拼进 shell 命令。因子示例（持仓则用 `tools.xyquant.query_stock_data({job_id})`）：

   ```js
   const job = await tools.xyquant.get_export({job_id, include_download:true});
   if (job?.id !== job_id || job.status !== "ready" || !job.download)
     return {status:"not_ready_or_not_authorized"};
   try {
     const res = await fetch(`http://127.0.0.1:${port}/mcp-job`, {
       method:"POST", redirect:"error",
       headers:{"Content-Type":"application/json", "X-XYQuant-Local":local_nonce},
       body:JSON.stringify(job)
     });
     const result = await res.json();
     return {http:res.status, status:result.status, code:result.code};
   } catch {
     return {status:"check_local_process_result"};
   }
   ```

3. `202 accepted` 仅表示任务交接已接受，不表示下载成功。本地进程用既有下载器和授权票据校验 HTTPS 下载（固定服务地址、不跟随重定向、校验文件大小及 SHA-256）。最终以**本地进程结束状态**为准。首个归因包较大，慢网下应留足本地后台任务时限；未完成就返回部分结果，不把未校验的临时文件当持仓。等待传输结束，不在传输中重发或重建任务。失败后按错误检查同一任务的状态和有效权限。

本地缓存只写校验后的数据和无凭据状态。因子的范围记录只保存任务号、日期、SHA-256 和「无股票筛选」标记。既有授权下载脚本不接受任意服务地址，处理期间不放宽来源、缓存或 OAuth 隔离。
