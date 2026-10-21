//! comfy-bridge 整合包桌面启动器：与 runtime/ 同目录分发，解压即用。
//!
//! 默认启动同级内置 Python 后端，并打开原生 WebView2 桌面窗口。
//! `--console` 保留原有命令行启动方式，供脚本与排障使用。
//! 不安装、不联网；整合包不含 ComfyUI 本体，数据面由用户已有的本地
//! ComfyUI 或 Modal 云端提供。

#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

use std::env;
use std::fs;
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{self, Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const LAUNCHER_VERSION: &str = env!("CARGO_PKG_VERSION");
const EMBEDDED_WHEEL_NAME: &str = env!("CB_EMBEDDED_WHEEL_NAME");
const BUNDLE_VERSION: &str = env!("CB_BUNDLE_VERSION");
const DEFAULT_HOST: &str = "127.0.0.1";
const DEFAULT_PORT: u16 = 18189;
const DEFAULT_PORT_CANDIDATES: [u16; 5] = [18189, 18190, 18191, 18192, 18193];

#[cfg(windows)]
mod win32 {
    use std::ffi::c_void;
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use std::ptr;

    #[link(name = "user32")]
    extern "system" {
        fn GetConsoleWindow() -> isize;
        fn ShowWindow(hwnd: isize, cmd: i32) -> i32;
        fn MessageBoxW(hwnd: isize, text: *const u16, caption: *const u16, kind: u32) -> i32;
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn AttachConsole(process_id: u32) -> i32;
        fn SetStdHandle(std_id: u32, handle: isize) -> i32;
        fn CreateFileW(
            name: *const u16,
            access: u32,
            share: u32,
            security: *mut c_void,
            creation: u32,
            flags: u32,
            template: isize,
        ) -> isize;
    }

    #[repr(C)]
    #[derive(Default)]
    struct JobObjectBasicLimitInformation {
        per_process_user_time_limit: i64,
        per_job_user_time_limit: i64,
        limit_flags: u32,
        minimum_working_set_size: usize,
        maximum_working_set_size: usize,
        active_process_limit: u32,
        affinity: usize,
        priority_class: u32,
        scheduling_class: u32,
    }

    #[repr(C)]
    #[derive(Default)]
    struct IoCounters {
        read_operation_count: u64,
        write_operation_count: u64,
        other_operation_count: u64,
        read_transfer_count: u64,
        write_transfer_count: u64,
        other_transfer_count: u64,
    }

    #[repr(C)]
    #[derive(Default)]
    struct JobObjectExtendedLimitInformation {
        basic_limit_information: JobObjectBasicLimitInformation,
        io_info: IoCounters,
        process_memory_limit: usize,
        job_memory_limit: usize,
        peak_process_memory_used: usize,
        peak_job_memory_used: usize,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(attributes: *mut c_void, name: *const u16) -> isize;
        fn SetInformationJobObject(
            job: isize,
            info_class: i32,
            info: *mut c_void,
            length: u32,
        ) -> i32;
        fn AssignProcessToJobObject(job: isize, process: isize) -> i32;
        fn TerminateJobObject(job: isize, exit_code: u32) -> i32;
        fn CloseHandle(handle: isize) -> i32;
    }

    const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: u32 = 0x0000_2000;
    const JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS: i32 = 9;
    const ATTACH_PARENT_PROCESS: u32 = u32::MAX;
    const STD_INPUT_HANDLE: u32 = u32::MAX - 10;
    const STD_OUTPUT_HANDLE: u32 = u32::MAX - 11;
    const STD_ERROR_HANDLE: u32 = u32::MAX - 12;
    const GENERIC_READ: u32 = 0x8000_0000;
    const GENERIC_WRITE: u32 = 0x4000_0000;
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const OPEN_EXISTING: u32 = 3;
    const INVALID_HANDLE_VALUE: isize = -1;

    fn wide(value: &str) -> Vec<u16> {
        OsStr::new(value).encode_wide().chain(Some(0)).collect()
    }

    pub fn hide_console() {
        // SW_HIDE：桌面模式不留下后台控制台窗口。
        unsafe {
            let hwnd = GetConsoleWindow();
            if hwnd != 0 {
                ShowWindow(hwnd, 0);
            }
        }
    }

    /// GUI 子系统的 release 构建没有标准输出；命令行模式下挂接父控制台。
    pub fn attach_console() -> bool {
        unsafe {
            if AttachConsole(ATTACH_PARENT_PROCESS) == 0 {
                return false;
            }
            let input = CreateFileW(
                wide("CONIN$").as_ptr(),
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                ptr::null_mut(),
                OPEN_EXISTING,
                0,
                0,
            );
            let output = CreateFileW(
                wide("CONOUT$").as_ptr(),
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                ptr::null_mut(),
                OPEN_EXISTING,
                0,
                0,
            );
            if input != INVALID_HANDLE_VALUE {
                SetStdHandle(STD_INPUT_HANDLE, input);
            }
            if output != INVALID_HANDLE_VALUE {
                SetStdHandle(STD_OUTPUT_HANDLE, output);
                SetStdHandle(STD_ERROR_HANDLE, output);
            }
            true
        }
    }

    /// 没有父控制台时仍保证 `--doctor` / `--version` 的结果可见。
    pub fn message_dialog(message: &str) {
        let text = wide(message);
        let caption = wide("ComfyBridge");
        // MB_OK | MB_ICONINFORMATION
        unsafe {
            MessageBoxW(0, text.as_ptr(), caption.as_ptr(), 0x40);
        }
    }

    pub fn error_dialog(message: &str) {
        let text = wide(message);
        let caption = wide("ComfyBridge");
        // MB_OK | MB_ICONERROR
        unsafe {
            MessageBoxW(0, text.as_ptr(), caption.as_ptr(), 0x10);
        }
    }

    /// Job 句柄保证关闭桌面应用时，Python 派生的监听进程也一起退出。
    pub struct Job(isize);

    impl Job {
        pub fn attach(child: &Child) -> Result<Self, String> {
            unsafe {
                let handle = CreateJobObjectW(ptr::null_mut(), ptr::null());
                if handle == 0 {
                    return Err(format!(
                        "创建后端进程组失败: {}",
                        std::io::Error::last_os_error()
                    ));
                }
                let job = Self(handle);
                let mut limits = JobObjectExtendedLimitInformation::default();
                limits.basic_limit_information.limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if SetInformationJobObject(
                    job.0,
                    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                    (&mut limits as *mut JobObjectExtendedLimitInformation).cast(),
                    std::mem::size_of::<JobObjectExtendedLimitInformation>() as u32,
                ) == 0
                {
                    return Err(format!(
                        "配置后端进程组失败: {}",
                        std::io::Error::last_os_error()
                    ));
                }
                if AssignProcessToJobObject(job.0, child.as_raw_handle() as isize) == 0 {
                    return Err(format!(
                        "绑定后端进程组失败: {}",
                        std::io::Error::last_os_error()
                    ));
                }
                Ok(job)
            }
        }

        pub fn terminate(&self) {
            unsafe {
                TerminateJobObject(self.0, 1);
            }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

enum Action {
    Help,
    Version,
    Doctor,
    Desktop,
    Console(Vec<String>),
    Run(Vec<String>),
}

/// launcher 旗标仅当第一个参数时生效，其余参数原样转发给 CLI。
fn classify_args(args: &[String]) -> Action {
    match args.first().map(String::as_str) {
        Some("-h") | Some("--help") => Action::Help,
        Some("--version") => Action::Version,
        Some("--doctor") => Action::Doctor,
        Some("--console") => Action::Console(args[1..].to_vec()),
        Some(flag) if flag.starts_with('-') => Action::Run(args.to_vec()),
        _ if args.is_empty() => Action::Desktop,
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

#[derive(Clone, Debug)]
struct Listen {
    host: String,
    port: u16,
    port_explicit: bool,
}

/// 桌面模式只解析本机监听参数；拒绝非回环地址，避免把桌面壳变成隐式远程服务。
fn desktop_listen(args: &[String]) -> Result<Listen, String> {
    let mut host = DEFAULT_HOST.to_string();
    let mut port = DEFAULT_PORT;
    let mut port_explicit = false;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--host" => {
                i += 1;
                host = args.get(i).ok_or("--host 缺少值")?.clone();
            }
            "--port" => {
                i += 1;
                let raw = args.get(i).ok_or("--port 缺少值")?;
                port = raw
                    .parse()
                    .map_err(|_| format!("--port 不是有效端口: {raw}"))?;
                port_explicit = true;
            }
            other => return Err(format!("桌面模式不支持参数: {other}")),
        }
        i += 1;
    }
    if host != DEFAULT_HOST {
        return Err("桌面模式仅支持本机监听；需要远程访问请使用 --console".to_string());
    }
    if port == 0 {
        return Err("--port 必须在 1-65535 之间".to_string());
    }
    Ok(Listen {
        host,
        port,
        port_explicit,
    })
}

fn port_in_use(listen: &Listen) -> bool {
    format!("{}:{}", listen.host, listen.port)
        .parse()
        .ok()
        .is_some_and(|addr| TcpStream::connect_timeout(&addr, Duration::from_millis(250)).is_ok())
}

/// 默认端口可自动回退，避免与 ComfyUI 或本机其它服务撞车；
/// 用户显式传入 `--port` 时必须完全尊重，不做静默替换。
fn resolve_listen(listen: &Listen, is_busy: impl Fn(&Listen) -> bool) -> Result<Listen, String> {
    if listen.port_explicit {
        if is_busy(listen) {
            return Err(format!(
                "端口 {} 已被占用。请释放端口，或更换 --port 后重试。\n\
                 排查: Get-NetTCPConnection -LocalPort {}",
                listen.port, listen.port
            ));
        }
        return Ok(listen.clone());
    }

    for port in DEFAULT_PORT_CANDIDATES {
        let candidate = Listen {
            host: listen.host.clone(),
            port,
            port_explicit: false,
        };
        if !is_busy(&candidate) {
            return Ok(candidate);
        }
    }

    Err(format!(
        "默认端口 {}-{} 均被占用。请释放其中一个端口，或用 --port 指定空闲端口。\n\
         排查: Get-NetTCPConnection -LocalPort 18189",
        DEFAULT_PORT_CANDIDATES[0],
        DEFAULT_PORT_CANDIDATES[DEFAULT_PORT_CANDIDATES.len() - 1]
    ))
}

fn spawn_backend(state: &BundleState, listen: &Listen) -> Result<Child, String> {
    let mut command = Command::new(python_exe(&state.runtime));
    isolate_env(&mut command);
    command
        .arg("-m")
        .arg("app.cli")
        .arg("--host")
        .arg(&listen.host)
        .arg("--port")
        .arg(listen.port.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // 桌面模式父进程是 GUI 子系统（没有控制台），而后端 python.exe 是控制台程序。
    // 不指定 CREATE_NO_WINDOW 时 Windows 会为它新建一个控制台窗口；该窗口会随
    // 常驻的 uvicorn 一起存在，即用户看到的那个黑框。Stdio::null() 只重定向句柄，
    // 并不阻止控制台分配，必须靠 creation flag 才能真正无窗口运行。
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
        .spawn()
        .map_err(|e| format!("无法启动内置后端: {e}"))
}

struct Backend {
    child: Child,
    #[cfg(windows)]
    job: win32::Job,
}

impl Backend {
    fn spawn(state: &BundleState, listen: &Listen) -> Result<Self, String> {
        let mut child = spawn_backend(state, listen)?;
        #[cfg(windows)]
        let job = match win32::Job::attach(&child) {
            Ok(job) => job,
            Err(e) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(e);
            }
        };
        Ok(Self {
            child,
            #[cfg(windows)]
            job,
        })
    }

    fn terminate(&mut self) {
        #[cfg(windows)]
        self.job.terminate();
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn wait_until_ready(listen: &Listen, child: &mut Child, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!("内置后端提前退出: {status}"));
        }
        if TcpStream::connect_timeout(
            &format!("{}:{}", listen.host, listen.port)
                .parse()
                .map_err(|e| format!("监听地址无效: {e}"))?,
            Duration::from_millis(250),
        )
        .is_ok()
        {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(150));
    }
    Err("等待内置后端启动超时".to_string())
}

fn run_desktop(args: &[String]) -> i32 {
    #[cfg(windows)]
    win32::hide_console();

    let fail = |message: String| -> i32 {
        eprintln!("[comfy-bridge] {message}");
        #[cfg(windows)]
        win32::error_dialog(&message);
        1
    };

    let state = inspect_bundle();
    if !state.python_ok || !state.app_ok || !state.gui_ok {
        return fail("整合包不完整，请运行 --doctor 检查并重新解压。".to_string());
    }
    let listen = match desktop_listen(args) {
        Ok(v) => v,
        Err(e) => return fail(e),
    };
    let listen = match resolve_listen(&listen, port_in_use) {
        Ok(v) => v,
        Err(e) => return fail(e),
    };
    if listen.port != DEFAULT_PORT {
        eprintln!(
            "[comfy-bridge] 默认端口 {DEFAULT_PORT} 被占用，已自动切换到 {}。",
            listen.port
        );
    }
    let mut backend = match Backend::spawn(&state, &listen) {
        Ok(v) => v,
        Err(e) => return fail(e),
    };
    if let Err(e) = wait_until_ready(&listen, &mut backend.child, Duration::from_secs(30)) {
        backend.terminate();
        return fail(e);
    }
    let url = format!("http://{}:{}/bridge/", listen.host, listen.port);
    let result = tauri::Builder::default()
        .setup(move |app| {
            let window = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::External(url.parse()?),
            )
            .title(format!("ComfyBridge - {}:{}", listen.host, listen.port))
            .inner_size(1280.0, 820.0)
            .min_inner_size(1024.0, 640.0)
            .build()?;
            window.show()?;
            Ok(())
        })
        .run(tauri::generate_context!());
    backend.terminate();
    match result {
        Ok(()) => 0,
        Err(e) => fail(format!("桌面窗口启动失败: {e}")),
    }
}

fn help_text() -> String {
    format!(
        "comfy-bridge 桌面整合包 {LAUNCHER_VERSION}\n\
\n\
用法: comfy-bridge [--host H] [--port P]\n\
      comfy-bridge --console [comfy-bridge CLI 参数...]\n\
\n\
launcher 选项（仅作为第一个参数时生效）:\n\
  --help        显示本帮助\n\
  --version     显示整合包版本\n\
  --doctor      自检整合包完整性（只读）\n\
  --console     不打开桌面窗口，按原 CLI 方式启动\n\
\n\
默认打开桌面窗口；可指定本机监听地址与端口:\n\
  comfy-bridge --port 18195\n\
  comfy-bridge --console --host 0.0.0.0 --port 18189\n\
\n\
默认端口 18189；被占用时自动尝试 18190-18193。显式 --port 不做自动回退。\n\
环境变量:\n\
  CB_BRIDGE_RUNTIME   指定 runtime 目录（默认 exe 同级 runtime/）\n"
    )
}

fn run_cli(mode: CliMode) -> i32 {
    #[cfg(all(windows, not(debug_assertions)))]
    {
        let attached = win32::attach_console();
        match mode {
            CliMode::Doctor => {
                let output = doctor_text();
                print!("{output}");
                if !attached {
                    win32::message_dialog(&output);
                }
            }
            CliMode::Version => {
                let output = version_text();
                print!("{output}");
                if !attached {
                    win32::message_dialog(&output);
                }
            }
            CliMode::Help => {
                let output = help_text();
                print!("{output}");
                if !attached {
                    win32::message_dialog(&output);
                }
            }
        }
    }
    #[cfg(not(all(windows, not(debug_assertions))))]
    match mode {
        CliMode::Doctor => doctor(),
        CliMode::Version => print_version(),
        CliMode::Help => print_help(),
    }
    0
}

#[derive(Clone, Copy)]
enum CliMode {
    Help,
    Version,
    Doctor,
}

fn version_text() -> String {
    let mut output = format!("launcher: {LAUNCHER_VERSION}\n");
    output.push_str(&format!(
        "bundle  : ComfyBridge {BUNDLE_VERSION}（{EMBEDDED_WHEEL_NAME}）\n"
    ));
    output
}

fn doctor_text() -> String {
    let mut output = version_text();
    let state = inspect_bundle();
    output.push_str(&format!(
        "exe 目录 : {}\n",
        exe_dir()
            .map(|d| d.display().to_string())
            .unwrap_or_default()
    ));
    output.push_str(&format!("runtime  : {}\n", state.runtime.display()));
    output.push_str(&format!(
        "python   : {}\n",
        if state.python_ok { "存在" } else { "缺失" }
    ));
    output.push_str(&format!(
        "app      : {}\n",
        if state.app_ok { "存在" } else { "缺失" }
    ));
    output.push_str(&format!(
        "gui      : {}\n",
        if state.gui_ok { "存在" } else { "缺失" }
    ));
    if state.python_ok {
        let probe = isolate_env(&mut Command::new(python_exe(&state.runtime)))
            .arg("--version")
            .output();
        match probe {
            Ok(o) => {
                let text = format!(
                    "{}{}",
                    String::from_utf8_lossy(&o.stdout),
                    String::from_utf8_lossy(&o.stderr)
                );
                output.push_str(&format!("版本     : {}\n", text.trim()));
            }
            Err(e) => output.push_str(&format!("版本     : 探测失败（{e}）\n")),
        }
    }
    if !(state.python_ok && state.app_ok && state.gui_ok) {
        output.push_str("提示     : 存在缺失项，请重新完整解压整合包。\n");
    }
    output
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    match classify_args(&args) {
        Action::Help => process::exit(run_cli(CliMode::Help)),
        Action::Version => process::exit(run_cli(CliMode::Version)),
        Action::Doctor => process::exit(run_cli(CliMode::Doctor)),
        Action::Desktop => process::exit(run_desktop(&[])),
        Action::Console(rest) => process::exit(run_app(&rest)),
        Action::Run(rest) => process::exit(run_desktop(&rest)),
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
        assert_eq!(
            wheel_version("comfy_bridge-0.1.0-py3-none-any.whl"),
            Some("0.1.0")
        );
        assert_eq!(
            wheel_version("foo_bar-1.2.3.dev1-b1-py3-none-any.whl"),
            Some("1.2.3.dev1")
        );
        assert_eq!(wheel_version("notawheel"), None);
        assert_eq!(wheel_version("a-.whl"), None);
    }

    #[test]
    fn classify_args_routes_launcher_flags() {
        assert!(matches!(
            classify_args(&args(&["--doctor"])),
            Action::Doctor
        ));
        assert!(matches!(
            classify_args(&args(&["--version"])),
            Action::Version
        ));
        assert!(matches!(classify_args(&args(&[])), Action::Desktop));
        assert!(matches!(
            classify_args(&args(&["--console"])),
            Action::Console(_)
        ));
        assert!(matches!(
            classify_args(&args(&["--port", "9"])),
            Action::Run(_)
        ));
    }

    #[test]
    fn desktop_listen_accepts_local_arguments() {
        let listen = desktop_listen(&args(&["--port", "18195"])).unwrap();
        assert_eq!(listen.host, "127.0.0.1");
        assert_eq!(listen.port, 18195);
        assert!(listen.port_explicit);
        assert!(desktop_listen(&args(&["--host", "0.0.0.0"])).is_err());
    }

    #[test]
    fn resolve_listen_falls_back_for_default_ports() {
        let listen = desktop_listen(&args(&[])).unwrap();
        let busy = |candidate: &Listen| candidate.port < 18191;
        let resolved = resolve_listen(&listen, busy).unwrap();
        assert_eq!(resolved.port, 18191);
    }

    #[test]
    fn resolve_listen_respects_explicit_port() {
        let listen = desktop_listen(&args(&["--port", "18189"])).unwrap();
        let resolved = resolve_listen(&listen, |_| true).unwrap_err();
        assert!(resolved.contains("端口 18189 已被占用"));
    }
}
