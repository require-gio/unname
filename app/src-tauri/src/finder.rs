//! Finder, iCloud Drive, and File Provider.
//!
//! Three narrow seams into the Mac file system:
//!
//! - **Tag**: Every saved copy carries the Finder tag "protected" (geschützt) or
//!   „protected". If that fails, the file is still saved; the caller logs the
//!   error in the diagnosis.
//! - **Read**: A file that exists only as a placeholder in iCloud Drive is not
//!   read but requested; the shell rejects it with `cloud_not_downloaded`.
//!   Loaded files are read via `NSFileCoordinator` so that no partially
//!   synchronized state arrives.
//! - **Target**: If a target folder is in iCloud Drive or with a File Provider
//!   (OneDrive, Dropbox …), every file there leaves the device.
//!
//! On other platforms, nothing is in the cloud, and reading happens directly.

use std::path::{Component, Path};

/// How a file appears before reading.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CloudState {
    /// Local or fully loaded.
    Local,
    /// Only a placeholder: The bytes are still in the cloud.
    NotDownloaded,
}

/// Is the path under `~/Library/Mobile Documents` (iCloud Drive) or
/// `~/Library/CloudStorage` (File Provider)? Read only from the path, without
/// the file system; the shell runs in the sandbox, `HOME` points there into the
/// container and is therefore not suitable as an anchor.
pub fn is_cloud_path(path: &Path) -> bool {
    let parts: Vec<&std::ffi::OsStr> = path
        .components()
        .filter_map(|component| match component {
            Component::Normal(part) => Some(part),
            _ => None,
        })
        .collect();
    parts.windows(2).any(|pair| {
        pair[0] == "Library" && (pair[1] == "Mobile Documents" || pair[1] == "CloudStorage")
    })
}

/// Does the content in this folder leave the device via a sync service?
///
/// The path alone is not enough: with "Schreibtisch und Dokumente in iCloud",
/// `~/Documents` remains in its usual location yet is still synchronized.
/// `NSURLIsUbiquitousItemKey` reports this.
pub fn is_cloud_location(directory: &Path) -> bool {
    if is_cloud_path(directory) {
        return true;
    }
    if let Ok(resolved) = std::fs::canonicalize(directory) {
        if is_cloud_path(&resolved) {
            return true;
        }
    }
    imp::is_ubiquitous(directory)
}

pub use imp::{read_coordinated, set_tag, start_download, state};

#[cfg(target_os = "macos")]
mod imp {
    use std::cell::RefCell;
    use std::path::{Path, PathBuf};
    use std::ptr::NonNull;

    use block2::StackBlock;
    use objc2::rc::Retained;
    use objc2::runtime::AnyObject;
    use objc2::AnyThread;
    use objc2_foundation::{
        NSArray, NSError, NSFileCoordinator, NSFileCoordinatorReadingOptions, NSFileManager,
        NSNumber, NSString, NSURLIsUbiquitousItemKey, NSURLResourceKey, NSURLTagNamesKey,
        NSURLUbiquitousItemDownloadingStatusCurrent, NSURLUbiquitousItemDownloadingStatusKey,
        NSURL,
    };

    use super::CloudState;

    fn file_url(path: &Path) -> Option<Retained<NSURL>> {
        NSURL::from_file_path(path)
    }

    fn resource(url: &NSURL, key: &NSURLResourceKey) -> Option<Retained<AnyObject>> {
        let mut value: Option<Retained<AnyObject>> = None;
        // SAFETY: `value` is an output parameter for an arbitrary object;
        // callers check its type with `downcast`.
        unsafe { url.getResourceValue_forKey_error(&mut value, key) }.ok()?;
        value
    }

    pub fn is_ubiquitous(path: &Path) -> bool {
        file_url(path)
            // SAFETY: immutable constants from Foundation.
            .and_then(|url| resource(&url, unsafe { NSURLIsUbiquitousItemKey }))
            .and_then(|value| value.downcast::<NSNumber>().ok())
            .is_some_and(|number| number.as_bool())
    }

    /// Is the file an unloaded iCloud placeholder? Everything that cannot be
    /// determined is considered local: Then `read_input` reads and reports a
    /// real read error instead of inventing a cloud.
    pub fn state(path: &Path) -> CloudState {
        let Some(url) = file_url(path) else {
            return CloudState::Local;
        };
        // SAFETY (here and below): immutable constants from Foundation.
        let ubiquitous = resource(&url, unsafe { NSURLIsUbiquitousItemKey })
            .and_then(|value| value.downcast::<NSNumber>().ok())
            .is_some_and(|number| number.as_bool());
        if !ubiquitous {
            return CloudState::Local;
        }
        let status = resource(&url, unsafe { NSURLUbiquitousItemDownloadingStatusKey })
            .and_then(|value| value.downcast::<NSString>().ok());
        let current = unsafe { NSURLUbiquitousItemDownloadingStatusCurrent };
        match status {
            Some(status) if !status.isEqualToString(current) => CloudState::NotDownloaded,
            _ => CloudState::Local,
        }
    }

    /// Requests iCloud to load the file. Does not wait for it.
    pub fn start_download(path: &Path) -> Result<(), String> {
        let url = file_url(path).ok_or("keine Datei-URL")?;
        NSFileManager::defaultManager()
            .startDownloadingUbiquitousItemAtURL_error(&url)
            .map_err(|error| error.localizedDescription().to_string())
    }

    /// Reads coordinated: A File Provider or iCloud must not swap the file
    /// mid-read. `read` gets the path released by the coordinator (usually the
    /// same one).
    pub fn read_coordinated<T>(path: &Path, read: impl FnOnce(&Path) -> T) -> Result<T, String> {
        let url = file_url(path).ok_or("keine Datei-URL")?;
        let coordinator =
            NSFileCoordinator::initWithFilePresenter(NSFileCoordinator::alloc(), None);
        let read = RefCell::new(Some(read));
        let result: RefCell<Option<T>> = RefCell::new(None);
        let accessor = StackBlock::new(|granted: NonNull<NSURL>| {
            // SAFETY: Foundation passes a valid URL that lives for the duration
            // of the synchronous call.
            let granted = unsafe { granted.as_ref() };
            let target = granted
                .to_file_path()
                .unwrap_or_else(|| PathBuf::from(path));
            if let Some(read) = read.borrow_mut().take() {
                *result.borrow_mut() = Some(read(&target));
            }
        });
        let mut error: Option<Retained<NSError>> = None;
        coordinator.coordinateReadingItemAtURL_options_error_byAccessor(
            &url,
            NSFileCoordinatorReadingOptions::WithoutChanges,
            Some(&mut error),
            &accessor,
        );
        if let Some(error) = error {
            return Err(error.localizedDescription().to_string());
        }
        result
            .into_inner()
            .ok_or_else(|| "Der Koordinator hat das Lesen nicht freigegeben.".to_string())
    }

    /// Sets exactly this one Finder tag. The file is freshly published and
    /// carries no tag yet.
    pub fn set_tag(path: &Path, name: &str) -> Result<(), String> {
        let url = file_url(path).ok_or("keine Datei-URL")?;
        let tags = NSArray::from_retained_slice(&[NSString::from_str(name)]);
        // SAFETY: `NSURLTagNamesKey` is a Foundation constant and expects an
        // `NSArray` of `NSString`.
        unsafe { url.setResourceValue_forKey_error(Some(&tags), NSURLTagNamesKey) }
            .map_err(|error| error.localizedDescription().to_string())
    }
}

#[cfg(not(target_os = "macos"))]
mod imp {
    use std::path::Path;

    use super::CloudState;

    pub fn is_ubiquitous(_path: &Path) -> bool {
        false
    }

    pub fn state(_path: &Path) -> CloudState {
        CloudState::Local
    }

    pub fn start_download(_path: &Path) -> Result<(), String> {
        Ok(())
    }

    pub fn read_coordinated<T>(path: &Path, read: impl FnOnce(&Path) -> T) -> Result<T, String> {
        Ok(read(path))
    }

    /// Finder tags exist only on macOS.
    pub fn set_tag(_path: &Path, _name: &str) -> Result<(), String> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn icloud_drive_and_file_provider_detected_by_path() {
        for cloud in [
            "/Users/anna/Library/Mobile Documents/com~apple~CloudDocs/Akten",
            "/Users/anna/Library/CloudStorage/OneDrive-Persönlich/Ordner",
            "/Users/anna/Library/CloudStorage/Dropbox",
        ] {
            assert!(is_cloud_path(Path::new(cloud)), "{cloud}");
        }
        for local in [
            "/Users/anna/Documents/Akten",
            "/Users/anna/Library/Containers/org.unname.desktop/Data",
            "/Volumes/Stick/Library",
            "/Users/anna/Mobile Documents",
        ] {
            assert!(!is_cloud_path(Path::new(local)), "{local}");
        }
    }

    #[test]
    fn local_file_is_not_placeholder_and_is_read() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("brief.txt");
        std::fs::write(&path, b"Hallo").unwrap();
        assert_eq!(state(&path), CloudState::Local);
        assert!(!is_cloud_location(directory.path()));
        let content = read_coordinated(&path, |granted| std::fs::read(granted).unwrap()).unwrap();
        assert_eq!(content, b"Hallo");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn tag_is_set() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("kopie.txt");
        std::fs::write(&path, b"x").unwrap();
        set_tag(&path, "geschützt").unwrap();
        let output = std::process::Command::new("xattr")
            .args(["-p", "com.apple.metadata:_kMDItemUserTags"])
            .arg(&path)
            .output()
            .unwrap();
        assert!(output.status.success());
    }
}
