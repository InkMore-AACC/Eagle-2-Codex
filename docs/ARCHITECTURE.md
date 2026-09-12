# 结构与维护

- plugin/web/index.html：本地图库界面、虚拟瀑布流、筛选、编辑和选择。
- plugin/scripts/hub.py：本地HTTP服务与MCP工具。图库端口18765，Eagle API端口41595。
- plugin/scripts/browse_backend.py：目录读取、搜索、筛选、排序；目录缓存60秒，编辑后失效。
- plugin/scripts/color_match.py：基于Oklab和颜色占比的匹配。
- plugin/scripts/edit_metadata.py：字段校验、并发修改检查、Eagle保存及回读。
- plugin/skills/eagle-reference/SKILL.md：当前任务素材读取及用户意图处理规则。
- install.py、helpers：便携安装和个人插件市场注册。

发布包的.mcp.json是模板，安装器写入本机解释器及脚本路径。不要将发布模板直接覆盖运行中的配置。

素材列表与任务绑定。更改任务ID不会迁移原任务选择。插件无聊天输入框附件控制能力。当前未提供离线队列。

性能改动：滚动时只更新变化的几何属性，尺寸拖动按帧合并，无标签提示词时使用固定标题高度；测量缓存上限3000逐项淘汰，选中项使用Set查找，名称排序缓存上限4096。保持现有业务判断，不以缓存替代编辑回读。
