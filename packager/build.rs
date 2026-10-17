//! 构建期读取 backend/dist 最新 wheel 名，用于启动器 --version / --doctor 展示。

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

fn main() {
    tauri_build::build();

    let manifest = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR 必须存在");
    let repo = Path::new(&manifest)
        .parent()
        .expect("packager 必须位于仓库根下");
    let dist = repo.join("backend").join("dist");

    let mut best: Option<(PathBuf, SystemTime)> = None;
    for entry in fs::read_dir(&dist).unwrap_or_else(|e| panic!("读取 {} 失败: {e}", dist.display()))
    {
        let entry = entry.expect("读取 dist 条目失败");
        let path = entry.path();
        let name = path.file_name().unwrap().to_string_lossy().into_owned();
        if name.starts_with("comfy_bridge-") && name.ends_with(".whl") {
            let mtime = entry
                .metadata()
                .and_then(|m| m.modified())
                .unwrap_or(SystemTime::UNIX_EPOCH);
            if best.as_ref().is_none_or(|(_, t)| mtime >= *t) {
                best = Some((path, mtime));
            }
        }
    }

    let name = match best {
        Some((wheel, _)) => wheel.file_name().unwrap().to_string_lossy().into_owned(),
        None => "comfy_bridge-unknown-py3-none-any.whl".to_string(),
    };
    println!("cargo:rustc-env=CB_EMBEDDED_WHEEL_NAME={name}");
    println!("cargo:rerun-if-changed={}", dist.display());
}
