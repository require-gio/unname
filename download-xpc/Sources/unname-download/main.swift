// XPC service org.unname.desktop.download.
//
// Message from the shell (dictionary, protocol 1):
//   protocol: int64 · url: string · hosts: [string] · offset: int64 ·
//   user_agent: string · fd: write end of a pipe
// The service writes one JSON line {"status","content_length"} into the pipe,
// then the bytes of the response, then closes it. The reply to the message
// comes only after that: {ok, code, message, http_status}.
//
// The service writes no file and holds on to nothing. Whether the bytes are
// correct is checked by the shell (size and SHA-256 from models.lock.json).

import DownloadCore
import Foundation
import XPC

/// One run: one address, one pipe, one reply.
final class Transfer: NSObject, URLSessionDataDelegate {
    private let request: DownloadRequest
    private let output: FileHandle
    private let finish: (Failure?, Int?) -> Void
    private var redirects = 0
    private var failure: Failure?
    private var status: Int?
    private var session: URLSession?

    init(request: DownloadRequest, output: FileHandle, finish: @escaping (Failure?, Int?) -> Void) {
        self.request = request
        self.output = output
        self.finish = finish
    }

    func start() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieAcceptPolicy = .never
        configuration.httpShouldSetCookies = false
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.waitsForConnectivity = false
        configuration.tlsMinimumSupportedProtocolVersion = .TLSv12
        // As in models.rs: 60 s without data abort the run, not the total duration.
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 24 * 60 * 60
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: queue)
        self.session = session

        var urlRequest = URLRequest(url: request.url)
        for (name, value) in request.headers {
            urlRequest.setValue(value, forHTTPHeaderField: name)
        }
        session.dataTask(with: urlRequest).resume()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest: URLRequest, completionHandler: @escaping (URLRequest?) -> Void)
    {
        if let refused = redirectAllowed(to: newRequest.url, count: redirects,
                                         policy: request.hostPolicy)
        {
            failure = refused
            completionHandler(nil)
            task.cancel()
            return
        }
        redirects += 1
        var next = newRequest
        for (name, value) in request.headers {
            next.setValue(value, forHTTPHeaderField: name)
        }
        completionHandler(next)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void)
    {
        guard failure == nil, let http = response as? HTTPURLResponse,
              let url = http.url, request.hostPolicy.allows(url: url)
        else {
            failure = failure ?? Failure(.hostNotAllowed, "Antwort von einem nicht erlaubten Host")
            completionHandler(.cancel)
            return
        }
        status = http.statusCode
        let length = http.expectedContentLength >= 0 ? http.expectedContentLength : nil
        guard write(ResponseHeader(status: http.statusCode, contentLength: length).line()) else {
            completionHandler(.cancel)
            return
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        if !write(data) { dataTask.cancel() }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        try? output.close()
        session.finishTasksAndInvalidate()
        self.session = nil
        if failure == nil, let error {
            failure = Failure(.network, error.localizedDescription)
        }
        finish(failure, status)
    }

    /// If the shell closes its end (cancel), writing fails with EPIPE; the run
    /// then ends as `cancelled`.
    private func write(_ data: Data) -> Bool {
        do {
            try output.write(contentsOf: data)
            return true
        } catch {
            failure = failure ?? Failure(.cancelled, "Die Huelle hat abgebrochen.")
            return false
        }
    }
}

/// Running transfers; the delegate queue holds no strong reference.
let transfers = NSMutableSet()
let transfersLock = NSLock()

func reply(to event: xpc_object_t, on peer: xpc_connection_t, failure: Failure?, status: Int?) {
    guard let reply = xpc_dictionary_create_reply(event) else { return }
    xpc_dictionary_set_bool(reply, "ok", failure == nil)
    if let failure {
        xpc_dictionary_set_string(reply, "code", failure.code.rawValue)
        xpc_dictionary_set_string(reply, "message", failure.message)
    }
    if let status { xpc_dictionary_set_int64(reply, "http_status", Int64(status)) }
    xpc_connection_send_message(peer, reply)
}

func strings(_ array: xpc_object_t?) -> [String]? {
    guard let array, xpc_get_type(array) == XPC_TYPE_ARRAY else { return nil }
    var values: [String] = []
    for index in 0 ..< xpc_array_get_count(array) {
        guard let pointer = xpc_array_get_string(array, index) else { return nil }
        values.append(String(cString: pointer))
    }
    return values
}

func handle(_ event: xpc_object_t, peer: xpc_connection_t) {
    let request: DownloadRequest
    do {
        request = try DownloadRequest(
            protocol: xpc_dictionary_get_int64(event, "protocol"),
            url: xpc_dictionary_get_string(event, "url").map { String(cString: $0) },
            hosts: strings(xpc_dictionary_get_value(event, "hosts")),
            offset: xpc_dictionary_get_int64(event, "offset"),
            userAgent: xpc_dictionary_get_string(event, "user_agent").map { String(cString: $0) }
        )
    } catch {
        reply(to: event, on: peer, failure: error, status: nil)
        return
    }
    let descriptor = xpc_dictionary_dup_fd(event, "fd")
    guard descriptor >= 0 else {
        reply(to: event, on: peer, failure: Failure(.invalidRequest, "Keine Pipe."), status: nil)
        return
    }
    let output = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    var transfer: Transfer?
    transfer = Transfer(request: request, output: output) { failure, status in
        reply(to: event, on: peer, failure: failure, status: status)
        transfersLock.lock()
        if let transfer { transfers.remove(transfer) }
        transfersLock.unlock()
        transfer = nil
    }
    transfersLock.lock()
    transfers.add(transfer!)
    transfersLock.unlock()
    transfer!.start()
}

// If the shell closes its pipe end, writing should fail and the service
// should keep running instead of dying from SIGPIPE.
signal(SIGPIPE, SIG_IGN)

xpc_main { peer in
    xpc_connection_set_event_handler(peer) { event in
        guard xpc_get_type(event) == XPC_TYPE_DICTIONARY else { return }
        handle(event, peer: peer)
    }
    xpc_connection_resume(peer)
}
