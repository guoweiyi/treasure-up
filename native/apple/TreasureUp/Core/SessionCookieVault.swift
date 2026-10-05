import Foundation
import Security

/// Only the session cookies enter Keychain. Passwords and CSRF tokens are never persisted.
struct SessionCookieVault {
    private let service = "com.guoweiyi.treasureup.session-cookies"

    func load(for server: URL) throws -> [HTTPCookie] {
        var query = query(for: server)
        query[kSecReturnData] = true
        query[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status != errSecItemNotFound else { return [] }
        guard status == errSecSuccess, let data = result as? Data else {
            throw VaultError(status: status)
        }
        let records = try JSONDecoder().decode([StoredCookie].self, from: data)
        return records.compactMap(\.cookie).filter {
            Self.belongsToServer($0, server: server) && ($0.expiresDate ?? .distantFuture) > Date()
        }
    }

    func save(_ cookies: [HTTPCookie], for server: URL) throws {
        let records = cookies.filter {
            Self.belongsToServer($0, server: server) && ($0.expiresDate ?? .distantFuture) > Date()
        }.map(StoredCookie.init)
        guard !records.isEmpty else { try clear(for: server); return }
        let data = try JSONEncoder().encode(records)
        let query = query(for: server)
        let attributes: [CFString: Any] = [kSecValueData: data]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var insert = query
            insert[kSecValueData] = data
            // Background playback may continue after the screen locks. This device's
            // keychain remains accessible after its first unlock and never syncs to iCloud.
            insert[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            let addStatus = SecItemAdd(insert as CFDictionary, nil)
            guard addStatus == errSecSuccess else { throw VaultError(status: addStatus) }
        } else if status != errSecSuccess {
            throw VaultError(status: status)
        }
    }

    func clear(for server: URL) throws {
        let status = SecItemDelete(query(for: server) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw VaultError(status: status) }
    }

    static func belongsToServer(_ cookie: HTTPCookie, server: URL) -> Bool {
        guard ["treasure_session", "treasure_viewer"].contains(cookie.name),
              let host = server.host?.lowercased() else { return false }
        return cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")).lowercased() == host
    }

    private func query(for server: URL) -> [CFString: Any] {
        [kSecClass: kSecClassGenericPassword, kSecAttrService: service,
         kSecAttrAccount: server.absoluteString, kSecAttrSynchronizable: false]
    }

    private struct StoredCookie: Codable {
        var name: String
        var value: String
        var domain: String
        var path: String
        var expires: Date?
        var secure: Bool
        var httpOnly: Bool

        init(_ cookie: HTTPCookie) {
            name = cookie.name
            value = cookie.value
            domain = cookie.domain
            path = cookie.path
            expires = cookie.expiresDate
            secure = cookie.isSecure
            httpOnly = cookie.isHTTPOnly
        }

        var cookie: HTTPCookie? {
            var properties: [HTTPCookiePropertyKey: Any] = [
                .name: name, .value: value, .domain: domain, .path: path,
                .secure: secure ? "TRUE" : "FALSE",
                HTTPCookiePropertyKey("HttpOnly"): httpOnly ? "TRUE" : "FALSE"
            ]
            if let expires { properties[.expires] = expires }
            return HTTPCookie(properties: properties)
        }
    }

    struct VaultError: LocalizedError {
        let status: OSStatus
        var errorDescription: String? { "无法访问设备钥匙串，请解锁设备后重试（\(status)）。" }
    }
}
