# 命运方舟助手 · 服务器版

多用户网页服务，账号、凭据、计划、运行历史按用户隔离。此包包含 Python 后端、已构建网页、前端源码、测试与 systemd 单元。部署机需 Python 3.9+；运行时只使用标准库。

## 账号管理与邮件提醒

2026-11-09 增加了 QQ 邮箱异常提醒、首次成功导入 token 后自动启用每小时随机分钟计划，以及仅管理员可见的用户与定时执行概览、删除账号功能。具体入口、权限、删除范围、统计规则及 QQ SMTP 配置见 [功能使用说明](../docs/服务器账号管理与邮件提醒.md)。这些功能面向最新服务器源码，四端 v0.3.4 下载包不包含本轮变更。真实 QQ 发信需由管理员填写发件邮箱及 SMTP 授权码。

## 多游戏账号（本 fork 新增）

每个用户可绑定多个游戏角色，以官方分享链接解析出的游戏角色 ID（`sRoleld`）作为唯一标识：

- 存储：`<用户目录>/accounts/<角色ID>/config-dashboard.json` 保存凭据，同目录下的 `pending-operation.json` 记录该角色的待确认操作；`<用户目录>/accounts.json` 只存角色名/区服等展示信息，不含 token。
- 接口：`GET /api/accounts` 列出已绑定角色；`POST /api/accounts/import` 添加或更新（同一角色需 `replace:true` 才允许更新）；`POST /api/accounts/delete` 解绑（删除凭据与待确认记录，保留运行历史）；`GET /api/status?account=<角色ID>` 查询指定角色，有多个角色时必须显式指定。
- 执行：定时任务逐个角色执行，时间槽为「分钟#角色ID」，各角色互不阻塞；某个角色的待确认记录只阻止它自己。
- 计划：每个角色有独立开关、频率和执行时间（存放在该用户 SQLite 的 `account_state` 表 `plan:<角色ID>`）。`GET /api/schedule` 返回 `accounts[]`（每个角色的 `plan` 与 `next_run`）与 `default_plan`；`POST /api/schedule` 必须带 `account`，只更新该角色。没有单独保存过的角色沿用用户默认计划，因此旧数据升级后行为不变；首次导入的新角色自动启用并分配随机分钟。
- 历史：`GET /api/history?account=<角色ID>` 按角色筛选，分页与统计规则不变。
- 兼容：旧版放在用户目录根下的 `config-dashboard.json` / `pending-operation.json` 会在首次访问时自动迁移到 `accounts/<角色ID>/`，无需手工迁移。
- 前端：账号管理页支持列出、切换、添加、解绑；总览与活动中心提供角色横条切换，顶栏有账号切换器；定时设置按角色一张卡（可单独保存或同步到其他角色）；运行记录可按角色与结果筛选，并标注所属角色；管理员用户列表显示每个用户的角色数与启用计划数。

## 部署

将本目录内容放入 /opt/lostark-dashboard；服务只监听 127.0.0.1:18745。服务单元使用 systemd `DynamicUser`，程序目录中的文件夹必须可遍历、普通文件必须可读取。Release 压缩包会统一写成目录 `0755`、文件 `0644`；若手工复制文件，部署后还要执行：

```bash
sudo find /opt/lostark-dashboard -type d -exec chmod 0755 {} +
sudo find /opt/lostark-dashboard -type f -exec chmod 0644 {} +
```

首次启动前在服务器本地创建私有初始化密码文件（不应覆盖已有 panel.key）：

```bash
sudo install -d -m 700 /var/lib/ark-dashboard
sudo sh -c 'umask 077; test -e /var/lib/ark-dashboard/panel.key || python3 -c "import secrets; print(secrets.token_urlsafe(32))" > /var/lib/ark-dashboard/panel.key'
sudo cp deploy/lostark-dashboard.service deploy/ark-dashboard-hourly.service deploy/ark-dashboard-hourly.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lostark-dashboard.service ark-dashboard-hourly.timer
```

初始化 ark 账号使用 panel.key 中的随机密码，服务启动后请在本机读取并妥善保管。其他用户可通过网页注册。已有部署直接保留整个 /var/lib/ark-dashboard，禁止把状态目录放入源码或发行包。

公网访问需要自行配置 HTTPS 反向代理，并将请求 Host 转为 127.0.0.1:18745、X-Forwarded-Proto 设为 https。使用新域名时通过 `sudo systemctl edit lostark-dashboard.service` 添加：

```ini
[Service]
Environment=ARK_PUBLIC_ORIGIN=https://你的域名
```

然后 daemon-reload 并重启服务。默认域名兼容既有部署。后端还支持 ARK_HELPER_DIR、ARK_STATIC_DIR、ARK_PORT；修改端口需同步代理。服务使用 systemd DynamicUser/StateDirectory 管理状态目录权限，不需要手工分发云端用户数据。

## 更新及执行行为

升级前备份代码与私有状态，暂停 timer，等待当前 hourly.service 执行完成，再替换代码、重启网页服务并恢复 timer。各账号的计划仍按北京时间执行。不要为测试手动触发真实签到/领奖。

活动2611支持任务1–13，6–11为推断参数：极焰沙漏、累计签到7/14/21/28天、首次查看规则。仅领取官方已完成(status=1)的任务，type=1；不会自动完成副本或规则查看条件。签到后刷新任务；每笔提交后确认status=2。

发生网络错误、超时或状态无法确认时立即停止，并保留用户目录中的 pending-operation.json。之后自动查询确认；仍不确定时提示前往“账号管理 → 更新我的 token”。成功保存同一游戏账号、同一角色的凭据后，自动清除该账号的待确认记录；下一次手动或定时执行先重新查询官方状态。导入失败、身份不符或保存失败均保留原记录。该提示不等于认定旧 token 已过期，导入过程也不会自动提交签到或领奖。

```bash
python3 -m unittest discover -s tests -v
```

单元测试使用假接口，不操作真实游戏。部署后用本机 HTTP 检查网页和登录响应。不要把“测试通过”解释为6–11已逐项真实领奖验证。
