// Validation rules of the download service. They mirror `host_allowed` and
// `check_url` in app/src-tauri/src/models.rs; the shell checks the same
// address beforehand, then the service checks it again, and every redirect.

import Foundation

public let protocolVersion: Int64 = 1
/// Same as `MAX_REDIRECTS` in models.rs.
public let maxRedirects = 5

/// Error codes, same as `ModelFailure.code` in the shell.
public enum FailureCode: String, Sendable {
    case invalidRequest = "invalid_request"
    case hostNotAllowed = "host_not_allowed"
    case network
    case cancelled
}

public struct Failure: Error, Equatable, Sendable {
    public let code: FailureCode
    public let message: String

    public init(_ code: FailureCode, _ message: String) {
        self.code = code
        self.message = message
    }
}

/// Allowed hosts of a source. `*.example.com` allows subdomains, not
/// `example.com` itself.
public struct HostPolicy: Sendable {
    public let hosts: [String]

    public init(hosts: [String]) {
        self.hosts = hosts
    }

    public func allows(host: String) -> Bool {
        let host = host.lowercased()
        return hosts.contains { pattern in
            if pattern.hasPrefix("*.") {
                return host.hasSuffix("." + pattern.dropFirst(2))
            }
            return host == pattern
        }
    }

    /// HTTPS only, allowed hosts only, no credentials in the address.
    public func allows(url: URL) -> Bool {
        guard url.scheme?.lowercased() == "https",
              let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil
        else { return false }
        return allows(host: host)
    }
}

/// A validated job from the shell.
public struct DownloadRequest: Sendable, Equatable {
    public let url: URL
    public let policy: [String]
    public let offset: Int64
    public let userAgent: String

    public init(protocol version: Int64, url raw: String?, hosts: [String]?, offset: Int64,
                userAgent: String?) throws(Failure)
    {
        guard version == protocolVersion else {
            throw Failure(.invalidRequest, "Protokoll \(version) wird nicht unterstuetzt.")
        }
        guard let hosts, !hosts.isEmpty, hosts.allSatisfy({ !$0.isEmpty }) else {
            throw Failure(.invalidRequest, "Keine erlaubten Hosts.")
        }
        guard let raw, let url = URL(string: raw) else {
            throw Failure(.invalidRequest, "Ungueltige Adresse.")
        }
        guard HostPolicy(hosts: hosts).allows(url: url) else {
            throw Failure(.hostNotAllowed, "Adresse nicht erlaubt: \(url.host ?? "")")
        }
        guard offset >= 0 else {
            throw Failure(.invalidRequest, "Ungueltiger Versatz.")
        }
        self.url = url
        policy = hosts
        self.offset = offset
        self.userAgent = (userAgent?.isEmpty == false) ? userAgent! : "unname"
    }

    public var hostPolicy: HostPolicy { HostPolicy(hosts: policy) }

    /// `Range` only when resuming; `identity` so that URLSession does not
    /// decompress anything: the checksum covers the bytes of the file exactly as
    /// the server delivers them.
    public var headers: [String: String] {
        var headers = ["Accept-Encoding": "identity", "User-Agent": userAgent]
        if offset > 0 { headers["Range"] = "bytes=\(offset)-" }
        return headers
    }
}

/// Decides on a redirect. `count` counts the ones so far.
public func redirectAllowed(to url: URL?, count: Int, policy: HostPolicy) -> Failure? {
    if count >= maxRedirects {
        return Failure(.network, "zu viele Weiterleitungen")
    }
    guard let url, policy.allows(url: url) else {
        return Failure(.hostNotAllowed, "Weiterleitung zu einem nicht erlaubten Host")
    }
    return nil
}

/// First line in the pipe, before the bytes of the file: status and length.
public struct ResponseHeader: Codable, Equatable, Sendable {
    public let status: Int
    public let contentLength: Int64?

    enum CodingKeys: String, CodingKey {
        case status
        case contentLength = "content_length"
    }

    public init(status: Int, contentLength: Int64?) {
        self.status = status
        self.contentLength = contentLength
    }

    public func line() -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        var data = (try? encoder.encode(self)) ?? Data("{\"status\":\(status)}".utf8)
        data.append(0x0A)
        return data
    }
}
