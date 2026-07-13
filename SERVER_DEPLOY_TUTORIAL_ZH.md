# workflow_delivery_package 服务器部署教程

这是一份给新手看的部署教程。

目标很简单：把这个项目部署到一台 Linux 服务器上，然后你可以直接在浏览器里打开后台页面使用。

这份教程优先使用 Docker 部署，因为它最省事，最适合小白。

## 一、这份教程适合谁

如果你符合下面这几条，就直接按本文做：

- 你本地是 Windows
- 服务器是 Ubuntu
- 你想把整个项目部署到服务器
- 你希望少折腾 Python、Node、依赖版本

## 二、最终部署完成后你能访问什么

部署成功后，默认可以打开这些页面：

- 登录页：`http://你的服务器IP:8098/login.html`
- 用户页：`http://你的服务器IP:8098/index.html`
- 管理页：`http://你的服务器IP:8098/admin.html`

默认管理员账号：

- 用户名：`admin`
- 密码：部署时设置的强密码（不要使用默认口令）

第一次登录后，建议马上修改密码。

## 三、部署前你需要准备什么

你需要准备 3 样东西：

1. 一台 Ubuntu 服务器
2. 服务器的登录信息
3. 本项目源码压缩包

如果你已经把源码打包好了，可以直接上传你刚刚生成的源码包。

## 四、整体流程先看一遍

整个部署流程只有 6 步：

1. 在服务器安装 Docker
2. 把源码包上传到服务器
3. 在服务器解压源码
4. 构建 Docker 镜像
5. 启动容器
6. 浏览器访问后台页面

## 五、第一步：登录服务器

### 方法 1：用 Windows Terminal / PowerShell 登录

打开 PowerShell，输入：

```powershell
ssh ubuntu@你的服务器IP
```

例如：

```powershell
ssh ubuntu@YOUR_SERVER_IP
```

第一次连接会提示你确认，输入：

```text
yes
```

然后输入服务器密码。

### 方法 2：用 Xshell / FinalShell / MobaXterm

如果你不想敲命令，也可以用图形化 SSH 工具登录服务器。

## 六、第二步：安装 Docker

登录服务器后，依次执行下面这些命令。

### 1. 更新软件包

```bash
sudo apt update
```

### 2. 安装 Docker

```bash
sudo apt install -y docker.io
```

### 3. 启动 Docker

```bash
sudo systemctl enable docker
sudo systemctl start docker
```

### 4. 确认 Docker 安装成功

```bash
sudo docker --version
```

如果能看到类似下面的输出，就说明装好了：

```text
Docker version xx.x.x
```

## 七、第三步：把源码包上传到服务器

你可以用两种办法上传。

### 方法 1：用 WinSCP 拖进去

这是最简单的办法。

1. 打开 WinSCP
2. 连接服务器
3. 进入服务器目录 `/opt`
4. 在本地找到源码压缩包
5. 直接拖到服务器里

建议你把压缩包传到：

```text
/opt/
```

### 方法 2：用 `scp` 命令上传

如果你的源码包在桌面，命令类似这样：

```powershell
scp C:\Users\你的用户名\Desktop\workflow_delivery_package_source_xxx.zip ubuntu@你的服务器IP:/opt/
```

## 八、第四步：在服务器解压源码

先进入 `/opt`：

```bash
cd /opt
```

如果服务器还没有 `unzip`，先安装：

```bash
sudo apt install -y unzip
```

解压源码包：

```bash
sudo unzip workflow_delivery_package_source_xxx.zip -d workflow_delivery_package
```

进入项目目录：

```bash
cd /opt/workflow_delivery_package
```

你可以用下面命令确认目录是否正确：

```bash
ls
```

如果能看到这些文件或目录，说明位置对了：

```text
Dockerfile
docker
webapp
tool_r18
requirements.txt
```

## 九、第五步：构建 Docker 镜像

在项目目录里执行：

```bash
cd /opt/workflow_delivery_package
sudo docker build -t workflow_delivery_package:latest .
```

这一步会比较久，因为它会自动安装：

- Python 依赖
- Node 依赖
- Playwright 浏览器依赖

第一次构建慢是正常的。

如果最后没有报错，就说明镜像构建成功了。

## 十、第六步：启动项目

先创建数据目录：

```bash
sudo mkdir -p /opt/workflow_delivery_package_data
```

然后启动容器：

```bash
sudo docker run -d \
  --name workflow_delivery_package \
  --restart unless-stopped \
  -p 8098:8098 \
  -v /opt/workflow_delivery_package_data:/data \
  -e TOOL_R18_PUBLIC_URL=http://你的服务器IP:8098 \
  workflow_delivery_package:latest
```

例如：

```bash
sudo docker run -d \
  --name workflow_delivery_package \
  --restart unless-stopped \
  -p 8098:8098 \
  -v /opt/workflow_delivery_package_data:/data \
  -e TOOL_R18_PUBLIC_URL=http://YOUR_SERVER_IP:8098 \
  workflow_delivery_package:latest
```

### 这条命令是什么意思

- `-d`：后台运行
- `--name workflow_delivery_package`：给容器起名字
- `--restart unless-stopped`：服务器重启后自动拉起
- `-p 8098:8098`：把网页端口映射出来
- `-v /opt/workflow_delivery_package_data:/data`：把数据单独保存，更新项目时不会丢
- `-e TOOL_R18_PUBLIC_URL=...`：告诉系统外部访问地址是什么

## 十一、第七步：检查项目是否启动成功

### 1. 看容器是否在运行

```bash
sudo docker ps
```

如果你能看到 `workflow_delivery_package`，状态是 `Up`，就说明容器已经跑起来了。

### 2. 看启动日志

```bash
sudo docker logs -f workflow_delivery_package
```

如果看到类似下面的信息，一般就说明启动正常：

- Web backend started
- Uvicorn running
- Tool_R18 daemon started

按 `Ctrl + C` 可以退出日志，不会停止容器。

### 3. 浏览器打开页面

在你的电脑浏览器里打开：

```text
http://你的服务器IP:8098/login.html
```

如果能打开登录页，就说明部署成功了。

## 十二、第八步：第一次登录后要做什么

第一次部署完成后，建议你马上做这几件事：

1. 登录后台
2. 修改管理员密码
3. 检查运行配置
4. 检查上传、发布、抓取这些功能的路径是否正确
5. 如果要对外生成链接，确认公网地址是否就是你设置的 `TOOL_R18_PUBLIC_URL`

## 十三、以后怎么更新项目

以后你更新代码，不需要重装服务器。

只需要重复下面这套流程：

### 1. 停掉旧容器

```bash
sudo docker stop workflow_delivery_package
sudo docker rm workflow_delivery_package
```

### 2. 替换源码

把新的源码包重新上传并解压覆盖到：

```text
/opt/workflow_delivery_package
```

### 3. 重新构建镜像

```bash
cd /opt/workflow_delivery_package
sudo docker build -t workflow_delivery_package:latest .
```

### 4. 重新启动容器

```bash
sudo docker run -d \
  --name workflow_delivery_package \
  --restart unless-stopped \
  -p 8098:8098 \
  -v /opt/workflow_delivery_package_data:/data \
  -e TOOL_R18_PUBLIC_URL=http://你的服务器IP:8098 \
  workflow_delivery_package:latest
```

注意：

- 只要你没有删 `/opt/workflow_delivery_package_data`
- 你的数据库、配置、运行数据一般都还在

## 十四、常用命令，建议收藏

### 查看运行中的容器

```bash
sudo docker ps
```

### 查看全部容器

```bash
sudo docker ps -a
```

### 查看日志

```bash
sudo docker logs -f workflow_delivery_package
```

### 重启容器

```bash
sudo docker restart workflow_delivery_package
```

### 停止容器

```bash
sudo docker stop workflow_delivery_package
```

### 删除容器

```bash
sudo docker rm workflow_delivery_package
```

## 十五、数据保存在哪里

这套 Docker 启动方式下，项目数据默认保存在这里：

```text
/opt/workflow_delivery_package_data
```

里面通常会有这些内容：

- webapp 数据
- 数据库
- 上传文件
- 运行配置
- tool_r18 运行目录

所以你要备份项目，优先备份这个目录。

## 十六、最常见的 5 个问题

### 问题 1：浏览器打不开页面

先检查：

```bash
sudo docker ps
```

如果容器没起来，再看日志：

```bash
sudo docker logs --tail 200 workflow_delivery_package
```

再检查服务器防火墙是否放行了 `8098` 端口。

### 问题 2：容器运行了，但外网打不开

大概率是下面几个原因：

1. 服务器安全组没开放 8098
2. Ubuntu 防火墙没放行 8098
3. 你访问的 IP 写错了

如果服务器开了 UFW，可以执行：

```bash
sudo ufw allow 8098/tcp
```

### 问题 3：更新后数据没了

一般是因为你没挂载数据目录，或者误删了：

```text
/opt/workflow_delivery_package_data
```

所以启动时一定要带上：

```text
-v /opt/workflow_delivery_package_data:/data
```

### 问题 4：后台链接地址不对

检查启动命令里的这个参数是不是写对了：

```text
-e TOOL_R18_PUBLIC_URL=http://你的服务器IP:8098
```

如果你后面换了域名，也要把这里一起改掉。

### 问题 5：源码上传后构建失败

先确认你进入的是正确目录：

```bash
cd /opt/workflow_delivery_package
```

然后确认这里确实有：

```text
Dockerfile
```

如果没有，说明你解压路径错了。

## 十七、如果你不用 Docker，还有没有别的办法

有，但不建议新手一开始就用。

因为源码直跑要自己处理这些东西：

- Python 虚拟环境
- Node 版本
- Playwright 依赖
- ffmpeg
- 进程守护
- 开机自启

这些都比 Docker 麻烦很多。

所以对于新手，结论很简单：

**优先用 Docker，不要一上来就走源码直跑。**

## 十八、一句话总结

如果你只记一套最简流程，就记下面这几步：

1. 服务器装 Docker
2. 上传源码包到 `/opt`
3. 解压到 `/opt/workflow_delivery_package`
4. 在项目目录执行 `docker build`
5. 用 `docker run` 启动
6. 浏览器打开 `http://服务器IP:8098/login.html`
