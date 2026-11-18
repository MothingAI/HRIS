#!/usr/bin/env python3
"""REV-007 目标绑定校验：执行任何 docker exec SQL 前，核验 API / app / DB
同属一个明确的 Compose 项目；测试写入路径额外拒绝主实例。

只比较容器名不够（不同项目可以有相似容器名，缺省更危险）。本模块用
Compose 所有权标签把三者钉死在同一部署上：

  1. NB_BASE 可达且 /api/app:getInfo 返回 200（应用在跑）；
  2. NB_BASE 的端口确实映射到 NB_APP_CONTAINER（docker port 核对）；
  3. NB_APP_CONTAINER 与 NB_DB_CONTAINER 的 com.docker.compose.project
     标签相同（同一 Compose 项目 = 同一 compose.yaml 部署）；
  4. 容器的 com.docker.compose.service 与 compose.yaml 服务名一致
     （app→app、DB→postgres，防止同项目里换成无关容器）。

REV-007 第二轮（11-18 复核）补强：同源 ≠ 可写。verify_business / verify_regression /
verify_reports / verify_unique_binding / demo_lowcode_employee_field 等会改状态的
套件，在【任何写入前】调用 require_explicit=True + forbid_main=True：

  a) 三个 NB_* 变量必须全部显式（缺省一律拒绝，不再有“默认主实例”路径）；
  b) NB_BASE 主机必须是回环地址（127.0.0.1 / localhost / ::1）——同端口的
     非本机 API 即使活着也不认（不只比端口数字）；
  c) 目标不得是主实例：compose.yaml 顶层 `name: hris` 决定主项目名恒为
     `hris`；显式指向主项目 / 主容器 / 主端口同样拒绝；
  d) 正确命名的隔离项目（hris-n4 / hris-doc / …）通过并放行注入，注入
     触发器仍由调用方在 finally 里清理。

用法：
  python scripts/target_binding.py --check          # 同源校验（seed 用；允许缺省=主实例）
  python scripts/target_binding.py --check --strict # 三变量必须全部显式
  python scripts/target_binding.py --check-write    # 写入门禁（显式+隔离+回环+非主实例）
"""
import json
import os
import subprocess
import sys
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:13000"
DEFAULT_APP = "hris-app-1"
DEFAULT_DB = "hris-postgres-1"
# 主部署标识：deploy/compose.yaml 顶层 `name: hris`（compose 项目名恒为 hris，
# 隔离实例必须用 -p hris-xxx 覆盖）。改 compose.yaml 的 name 时同步改这里。
MAIN_PROJECT = "hris"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


class TargetMismatch(RuntimeError):
    """API/app/DB 目标不一致或无法证明同源——写入前拒绝。"""


def _docker_inspect(container):
    r = subprocess.run(["docker", "inspect", container],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise TargetMismatch(
            f"容器不存在或 docker 不可用: {container} ({r.stderr.strip()[:120]})")
    return json.loads(r.stdout)[0]


def compose_label(container, label):
    info = _docker_inspect(container)
    return (info.get("Config", {}).get("Labels") or {}).get(label)


def host_ports(container):
    """容器映射到宿主机的端口集合（docker port）。"""
    r = subprocess.run(["docker", "port", container], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise TargetMismatch(f"docker port 失败: {container}")
    ports = set()
    for line in r.stdout.splitlines():
        # 形如 80/tcp -> 127.0.0.1:13004
        if "->" in line:
            host = line.split("->", 1)[1].strip()
            if ":" in host:
                try:
                    ports.add(int(host.rsplit(":", 1)[1]))
                except ValueError:
                    pass
    return ports


def base_url_parts(base):
    """拆 (scheme, host, port)。host 保留 IPv6 方括号原文之外的小写形式。"""
    base = (base or DEFAULT_BASE).strip().rstrip("/")
    rest = base.split("//", 1)[-1]
    netloc = rest.split("/", 1)[0]
    if netloc.startswith("[") and "]" in netloc:  # [::1]:8080
        host, _, port_s = netloc[1:].partition("]")
        port_s = port_s.lstrip(":")
    else:
        host, _, port_s = netloc.partition(":")
    try:
        port = int(port_s) if port_s else (443 if base.startswith("https") else 80)
    except ValueError:
        port = 443 if base.startswith("https") else 80
    return base.split("//", 1)[0].rstrip(":"), host.lower(), port


def base_port(base):
    return base_url_parts(base)[2]


def base_host(base):
    return base_url_parts(base)[1]


def api_alive(base):
    url = (base or DEFAULT_BASE).strip().rstrip("/") + "/api/app:getInfo"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def resolve_targets(env):
    """返回 (base, app, db, explicit)。explicit=三变量是否全部显式提供。"""
    base = env.get("NB_BASE", "").strip() or DEFAULT_BASE
    app = env.get("NB_APP_CONTAINER", "").strip() or DEFAULT_APP
    db = env.get("NB_DB_CONTAINER", "").strip() or DEFAULT_DB
    explicit = all(env.get(k, "").strip() for k in
                   ("NB_BASE", "NB_APP_CONTAINER", "NB_DB_CONTAINER"))
    return base, app, db, explicit


def verify_target(env, purpose="SQL 操作", require_explicit=False, forbid_main=False):
    """校验 API/app/DB 同源（+ 可选的测试写入门禁）。

    通过返回描述 dict；不一致抛 TargetMismatch。

    - require_explicit=True：三个变量必须全部显式——防止缺省悄悄落到主实例。
    - forbid_main=True（测试写入路径）：还要求回环主机 + 非主项目/主容器/主端口。
      同源校验通过 ≠ 允许写入：显式指向主实例同样在写入前被拒绝。
    """
    base, app, db, explicit = resolve_targets(env)

    if require_explicit and not explicit:
        raise TargetMismatch(
            f"{purpose}: 拒绝执行——NB_BASE / NB_APP_CONTAINER / NB_DB_CONTAINER "
            "必须全部显式指向同一隔离实例（不允许缺省）")

    problems = []
    if not api_alive(base):
        problems.append(f"API 不可达: {base}/api/app:getInfo")

    proj_app = compose_label(app, "com.docker.compose.project")
    proj_db = compose_label(db, "com.docker.compose.project")
    if proj_app is None or proj_db is None:
        problems.append(f"容器缺少 Compose 项目标签: app={proj_app} db={proj_db}")
    elif proj_app != proj_db:
        problems.append(
            f"app 与 DB 不在同一 Compose 项目: {app}={proj_app} vs {db}={proj_db}")

    # 服务归属：容器必须是 compose.yaml 里的 app / postgres 服务（防同项目换容器）
    svc_app = compose_label(app, "com.docker.compose.service")
    svc_db = compose_label(db, "com.docker.compose.service")
    if svc_app is not None and svc_app != "app":
        problems.append(f"{app} 不是 app 服务（service={svc_app}）")
    if svc_db is not None and svc_db != "postgres":
        problems.append(f"{db} 不是 postgres 服务（service={svc_db}）")

    try:
        ports = host_ports(app)
        if base_port(base) not in ports:
            problems.append(
                f"NB_BASE 端口 {base_port(base)} 未映射到 {app}（其宿主端口: {sorted(ports)}）")
    except TargetMismatch as e:
        problems.append(str(e))

    # ---- 测试写入门禁：同源还不够，必须回环 + 非主实例 ----
    if forbid_main and not problems:
        host = base_host(base)
        if host not in LOOPBACK_HOSTS:
            problems.append(
                f"NB_BASE 主机 {host} 不是回环地址——测试写入只允许本机隔离实例")
        if proj_app == MAIN_PROJECT:
            problems.append(
                f"目标 {proj_app} 是主部署项目（compose.yaml name={MAIN_PROJECT}）"
                "——测试写入被拒绝")
        if base.rstrip("/") == DEFAULT_BASE or app == DEFAULT_APP or db == DEFAULT_DB:
            problems.append(
                f"目标命中主实例缺省值（base={DEFAULT_BASE} app={DEFAULT_APP} "
                f"db={DEFAULT_DB}）——测试写入被拒绝")

    if problems:
        raise TargetMismatch(f"{purpose}: 目标绑定校验失败 -> " + "; ".join(problems))

    return {"base": base, "app": app, "db": db,
            "project": proj_app, "explicit": explicit}


def main():
    args = sys.argv[1:]
    env = {k: v for k, v in os.environ.items() if v}
    # 也读 .env（本地缺省），环境变量优先
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env.setdefault(k, v)
    write_gate = "--check-write" in args
    try:
        info = verify_target(
            env,
            "测试写入门禁" if write_gate else "seed 前置校验",
            require_explicit=write_gate or "--strict" in args,
            forbid_main=write_gate)
    except TargetMismatch as e:
        print(f"[FAIL] {e}", flush=True)
        return 1
    mode = ("写入门禁（显式隔离实例，非主项目）" if write_gate
            else ("显式" if info["explicit"] else "缺省（主实例）"))
    print(f"[OK] 目标绑定一致: base={info['base']} app={info['app']} "
          f"db={info['db']} project={info['project']} ({mode})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
