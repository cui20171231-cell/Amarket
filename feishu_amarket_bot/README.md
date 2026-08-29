# Amarket Bot 飞书长连接监听器

本程序使用飞书官方 Python SDK，通过 WebSocket 长连接接收
`im.message.receive_v1` 事件，不需要安装飞书客户端。

统一触发指令前缀为 `/A`。例如 `/A 10:15` 表示将参数 `10:15`
交给后续对应流程；后续指令统一按 `/A + 参数` 扩展。

## 文件位置

- 程序：`D:\Amarket\feishu_amarket_bot`
- 密钥：`.env`（仅保存在本机，已排除版本管理）
- 日志：`logs\listener.log`

## 飞书开放平台需要设置

1. 为应用启用“机器人”能力。
2. 在“事件与回调”中选择“使用长连接接收事件”。
3. 添加事件“接收消息”，事件名为 `im.message.receive_v1`。
4. 在“权限管理”中开通接收机器人消息所需权限。
5. 创建并发布新版本，使权限和事件配置生效。
6. 把机器人加入要测试的群，在群里 @机器人发送消息。

## 本机操作

1. 运行 `configure_credentials.ps1`，输入 APP_ID 和 APP_SECRET。
2. 运行 `run_foreground.ps1`，在前台验证是否收到消息。
3. 验证通过后运行 `install_autostart.ps1`，安装当前用户登录后的开机自启任务。

卸载自启任务时运行 `uninstall_autostart.ps1`。
