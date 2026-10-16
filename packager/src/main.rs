//! comfy-bridge 整合包启动器：与 runtime/ 同目录分发，解压即用。
//!
//! exe 只做一件事：定位同级内置 Python，把参数转发给 `python -m app.cli`。
//! 不安装、不联网；整合包不含 ComfyUI 本体，数据面由用户已有的本地
//! ComfyUI 或 Modal 云端提供。

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{self, Command};

const LAUNCHER_VERSION: &str = env!("CARGO_PKG_VERSION");
const EMBEDDED_WHEEL_NAME: &str = env!("CB_EMBEDDED_WHEEL_NAME");

enum Action {
    Help,
    Version,
    Doctor,
    Run(Vec<String>),
}

/// launcher 旗标仅当第一个参数时生效，其余参数原样转发给 CLI。
fn classify_args(args: &[String]) -> Action {
    match args.first().map(String::as_str) {
        Some("-h") | Some("--help") => Action::Help,
        Some("--version") => Action::Version,
        Some("--doctor") => Action::Doctor,
        _ => Action::Run(args.to_vec()),
    }
}

/// wheel 文件名规范：{dist}-{version}(-{build})-{python}-{abi}-{platform}.whl。
fn wheel_version(name: &str) -> Option<&str> {
    let base = name.strip_suffix(".whl")?;
    let mut parts = base.split('-');
    let dist = parts.next()?;
    let version = parts.next()?;
    if dist.is_empty() || version.is_empty() || parts.count() < 3 {
        return None;
    }
    Some(version)
}

fn exe_dir() -> Result<PathBuf, String> {
    env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .ok_or_else(|| "无法定位 exe 所在目录".to_string())
}

fn runtime_dir() -> PathBuf {
    if let Ok(custom) = env::var("CB_BRIDGE_RUNTIME") {
        return PathBuf::from(custom);
    }
    exe_dir()
        .map(|dir| dir.join("runtime"))
        .unwrap_or_else(|_| PathBuf::from("runtime"))
}

fn python_exe(runtime: &Path) -> PathBuf {
    if cfg!(windows) {
        runtime.join("python.exe")
    } else {
        runtime.join("bin").join("python3")
    }
}

fn site_packages(runtime: &Path) -> Option<PathBuf> {
    if cfg!(windows) {
        let sp = runtime.join("Lib").join("site-packages");
        return sp.is_dir().then_some(sp);
    }
    // linux/macOS 布局：lib/python3.x/site-packages
    let lib = runtime.join("lib");
    fs::read_dir(&lib)
        .ok()?
        .flatten()
        .map(|e| e.path().join("site-packages"))
        .find(|p| p.is_dir())
}

struct BundleState {
    runtime: PathBuf,
    python_ok: bool,
    app_ok: bool,
    gui_ok: bool,
}

fn inspect_bundle() -> BundleState {
    let runtime = runtime_dir();
    let python_ok = python_exe(&runtime).is_file();
    let (app_ok, gui_ok) = site_packages(&runtime)
        .map(|sp| {
            let app = sp.join("app");
            (
                app.is_dir(),
                app.join("static").join("index.html").is_file(),
            )
        })
        .unwrap_or((false, false));
    BundleState {
        runtime,
        python_ok,
        app_ok,
        gui_ok,
    }
}

/// 隔离宿主机残留的 Python 环境变量，避免干扰内置运行时。
fn isolate_env(cmd: &mut Command) -> &mut Command {
    cmd.env_remove("PYTHONHOME").env_remove("PYTHONPATH")
}

fn run_app(args: &[String]) -> i32 {
    let state = inspect_bundle();
    if !state.python_ok {
        eprintln!(
            "[comfy-bridge] 整合包不完整：缺少 {}。请完整解压整合包后重试。",
            python_exe(&state.runtime).display()
        );
        return 2;
    }
    if !state.app_ok {
        eprintln!(
            "[comfy-bridge] 整合包不完整：runtime 内缺少应用包。请重新解压或重新构建整合包。"
        );
        return 2;
    }
    let status = isolate_env(&mut Command::new(python_exe(&state.runtime)))
        .arg("-m")
        .arg("app.cli")
        .args(args)
        .status();
    match status {
        Ok(status) => status.code().unwrap_or(1),
        Err(e) => {
            eprintln!("[comfy-bridge] 启动失败: {e}");
            1
        }
    }
}

fn print_help() {
    println!("comfy-bridge 整合包启动器 {LAUNCHER_VERSION}");
    println!();
    println!("用法: comfy-bridge [launcher 选项] [comfy-bridge CLI 参数…]");
    println!();
    println!("launcher 选项（仅作为第一个参数时生效）:");
    println!("  --help        显示本帮助");
    println!("  --version     显示整合包版本");
    println!("  --doctor      自检整合包完整性（只读）");
    println!();
    println!("其余参数原样转发给 comfy-bridge CLI，例如:");
    println!("  comfy-bridge --port 8189");
    println!("  comfy-bridge --host 0.0.0.0 --port 8189   # 非回环监听需配置管理令牌");
    println!();
    println!("环境变量:");
    println!("  CB_BRIDGE_RUNTIME   指定 runtime 目录（默认 exe 同级 runtime/）");
}

fn print_version() {
    println!("launcher: {LAUNCHER_VERSION}");
    let version = wheel_version(EMBEDDED_WHEEL_NAME).unwrap_or("?");
    println!("bundle  : ComfyBridge {version}（{EMBEDDED_WHEEL_NAME}）");
}

fn doctor() {
    print_version();
    let state = inspect_bundle();
    println!(
        "exe 目录 : {}",
        exe_dir().map(|d| d.display().to_string()).unwrap_or_default()
    );
    println!("runtime  : {}", state.runtime.display());
    println!("python   : {}", if state.python_ok { "存在" } else { "缺失" });
    println!("app      : {}", if state.app_ok { "存在" } else { "缺失" });
    println!("gui      : {}", if state.gui_ok { "存在" } else { "缺失" });
    if state.python_ok {
        let output = isolate_env(&mut Command::new(python_exe(&state.runtime)))
            .arg("--version")
            .output();
        match output {
            Ok(o) => {
                let text = format!(
                    "{}{}",
                    String::from_utf8_lossy(&o.stdout),
                    String::from_utf8_lossy(&o.stderr)
                );
                println!("版本     : {}", text.trim());
            }
            Err(e) => println!("版本     : 探测失败（{e}）"),
        }
    }
    if !(state.python_ok && state.app_ok && state.gui_ok) {
        println!("提示     : 存在缺失项，请重新完整解压整合包。");
    }
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    match classify_args(&args) {
        Action::Help => print_help(),
        Action::Version => print_version(),
        Action::Doctor => doctor(),
        Action::Run(rest) => process::exit(run_app(&rest)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn wheel_version_parses_standard_names() {
        assert_eq!(wheel_version("comfy_bridge-0.1.0-py3-none-any.whl"), Some("0.1.0"));
        assert_eq!(wheel_version("foo_bar-1.2.3.dev1-b1-py3-none-any.whl"), Some("1.2.3.dev1"));
        assert_eq!(wheel_version("notawheel"), None);
        assert_eq!(wheel_version("a-.whl"), None);
    }

    #[test]
    fn classify_args_routes_launcher_flags() {
        assert!(matches!(classify_args(&args(&["--doctor"])), Action::Doctor));
        assert!(matches!(classify_args(&args(&["--version"])), Action::Version));
        assert!(matches!(classify_args(&args(&["--port", "9"])), Action::Run(_)));
        assert!(matches!(classify_args(&args(&[])), Action::Run(_)));
    }
}
