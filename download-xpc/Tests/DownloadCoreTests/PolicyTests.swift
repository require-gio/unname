import DownloadCore
import Foundation
import XCTest

final class PolicyTests: XCTestCase {
    let hosts = ["huggingface.co", "*.hf.co"]

    func testHostsMatchTheShell() {
        let policy = HostPolicy(hosts: hosts)
        XCTAssertTrue(policy.allows(host: "huggingface.co"))
        XCTAssertTrue(policy.allows(host: "HuggingFace.CO"))
        XCTAssertTrue(policy.allows(host: "cdn-lfs.hf.co"))
        XCTAssertFalse(policy.allows(host: "hf.co"), "*.x does not allow x itself")
        XCTAssertFalse(policy.allows(host: "evilhf.co"))
        XCTAssertFalse(policy.allows(host: "huggingface.co.evil.example"))
    }

    func testHttpsOnlyAndNoCredentials() {
        let policy = HostPolicy(hosts: hosts)
        XCTAssertTrue(policy.allows(url: URL(string: "https://huggingface.co/a/b")!))
        XCTAssertFalse(policy.allows(url: URL(string: "http://huggingface.co/a")!))
        XCTAssertFalse(policy.allows(url: URL(string: "https://user:pw@huggingface.co/a")!))
        XCTAssertFalse(policy.allows(url: URL(string: "https://user@huggingface.co/a")!))
        XCTAssertFalse(policy.allows(url: URL(string: "file:///etc/passwd")!))
    }

    func testRequestIsValidated() throws {
        let request = try DownloadRequest(protocol: 1, url: "https://huggingface.co/x", hosts: hosts,
                                          offset: 10, userAgent: "unname/0.1.0")
        XCTAssertEqual(request.headers["Range"], "bytes=10-")
        XCTAssertEqual(request.headers["Accept-Encoding"], "identity")
        XCTAssertEqual(request.headers["User-Agent"], "unname/0.1.0")

        let fresh = try DownloadRequest(protocol: 1, url: "https://huggingface.co/x", hosts: hosts,
                                        offset: 0, userAgent: nil)
        XCTAssertNil(fresh.headers["Range"])

        func code(_ make: () throws -> DownloadRequest) -> FailureCode? {
            do {
                _ = try make()
                return nil
            } catch {
                return (error as? Failure)?.code
            }
        }
        XCTAssertEqual(code { try DownloadRequest(protocol: 2, url: "https://huggingface.co/x",
                                                  hosts: hosts, offset: 0, userAgent: nil) },
                       .invalidRequest)
        XCTAssertEqual(code { try DownloadRequest(protocol: 1, url: "https://evil.example/x",
                                                  hosts: hosts, offset: 0, userAgent: nil) },
                       .hostNotAllowed)
        XCTAssertEqual(code { try DownloadRequest(protocol: 1, url: "https://huggingface.co/x",
                                                  hosts: [], offset: 0, userAgent: nil) },
                       .invalidRequest)
        XCTAssertEqual(code { try DownloadRequest(protocol: 1, url: nil, hosts: hosts,
                                                  offset: 0, userAgent: nil) },
                       .invalidRequest)
        XCTAssertEqual(code { try DownloadRequest(protocol: 1, url: "https://huggingface.co/x",
                                                  hosts: hosts, offset: -1, userAgent: nil) },
                       .invalidRequest)
    }

    func testRedirects() {
        let policy = HostPolicy(hosts: hosts)
        XCTAssertNil(redirectAllowed(to: URL(string: "https://cdn-lfs.hf.co/x"), count: 0,
                                     policy: policy))
        XCTAssertEqual(redirectAllowed(to: URL(string: "https://evil.example/x"), count: 0,
                                       policy: policy)?.code, .hostNotAllowed)
        XCTAssertEqual(redirectAllowed(to: URL(string: "http://cdn-lfs.hf.co/x"), count: 0,
                                       policy: policy)?.code, .hostNotAllowed)
        XCTAssertEqual(redirectAllowed(to: URL(string: "https://cdn-lfs.hf.co/x"),
                                       count: maxRedirects, policy: policy)?.code, .network)
    }

    func testHeaderIsOneLine() throws {
        let line = ResponseHeader(status: 206, contentLength: 42).line()
        XCTAssertEqual(String(decoding: line, as: UTF8.self), "{\"content_length\":42,\"status\":206}\n")
        let without = ResponseHeader(status: 200, contentLength: nil).line()
        XCTAssertEqual(String(decoding: without, as: UTF8.self), "{\"status\":200}\n")
    }
}
