# 前端第三方库本地存放目录

按设计文档 Q8 决策，three.js 走本地 vendor 而非 CDN，确保平板在内网/弱网环境下也能用。

## 需要放置的文件

从 https://unpkg.com/three@0.160.0/build/three.module.min.js 下载（或选用 ≥0.150 的任意版本）：

```
vendor/
├── three.module.min.js
└── three-addons/
    └── controls/
        └── OrbitControls.js
```

`OrbitControls.js` 来自 https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js

## 下载命令（在本目录执行）

```bash
mkdir -p three-addons/controls
curl -L -o three.module.min.js \
    https://unpkg.com/three@0.160.0/build/three.module.min.js
curl -L -o three-addons/controls/OrbitControls.js \
    https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js
```

下载后大概 ~700KB，syncpi 一次到开发板就行，以后不变。

## 为什么不直接 commit 进仓库

避免把第三方代码混进 git 历史；如果你不介意可以提交。
