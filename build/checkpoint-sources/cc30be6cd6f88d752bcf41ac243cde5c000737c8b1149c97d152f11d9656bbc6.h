// Copyright 2012 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_P2P_PORT_ALLOCATOR_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_P2P_PORT_ALLOCATOR_H_

#include <atomic>
#include <memory>

#include "base/memory/ref_counted.h"
#include "base/memory/scoped_refptr.h"
#include "third_party/blink/renderer/platform/platform_export.h"
#include "third_party/webrtc/p2p/client/basic_port_allocator.h"

namespace webrtc {
class AsyncDnsResolverFactoryInterface;
class AsyncPacketSocket;
class PacketSocketFactory;
class LocalNetworkAccessPermissionFactoryInterface;
}  // namespace webrtc

namespace blink {

// One instance belongs to one ExecutionContext, never a process-global
// registry. UDP endpoints in this context are memory queues, not OS sockets.
// Invalidate() is the only cross-thread operation; queued deliveries recheck
// revocation when the execution context is destroyed.
class PLATFORM_EXPORT DocumentLoopbackContext
    : public base::RefCountedThreadSafe<DocumentLoopbackContext> {
 public:
  DocumentLoopbackContext();
  void Invalidate();
  bool IsAlive() const;
  bool IsBoundEndpoint(const webrtc::SocketAddress& address) const;
  // Keep real TCP destinations disjoint from the virtual UDP namespace. A
  // permission exemption for an internal endpoint must never authorize OS I/O.
  bool ClaimNativeTcpDestination(const webrtc::SocketAddress& address);
  std::unique_ptr<webrtc::AsyncPacketSocket> CreateSocket(
      const webrtc::Environment& env,
      const webrtc::SocketAddress& address,
      uint16_t min_port,
      uint16_t max_port);

 private:
  friend class base::RefCountedThreadSafe<DocumentLoopbackContext>;
  ~DocumentLoopbackContext();
  class State;
  std::atomic<bool> alive_{true};
  std::unique_ptr<State> state_;
};

PLATFORM_EXPORT bool DocumentLoopbackEnabled();
PLATFORM_EXPORT std::unique_ptr<webrtc::NetworkManager>
CreateDocumentLoopbackNetworkManager(
    scoped_refptr<DocumentLoopbackContext> context);
PLATFORM_EXPORT std::unique_ptr<webrtc::PacketSocketFactory>
CreateDocumentLoopbackSocketFactory(
    scoped_refptr<DocumentLoopbackContext> context,
    webrtc::PacketSocketFactory* native_factory);
// Peer ICE hostnames must not turn an internal candidate into a DNS/mDNS query.
// TURN server resolution retains its separate native socket-factory path.
PLATFORM_EXPORT std::unique_ptr<webrtc::AsyncDnsResolverFactoryInterface>
CreateDocumentLoopbackPeerResolverFactory();
PLATFORM_EXPORT
std::unique_ptr<webrtc::LocalNetworkAccessPermissionFactoryInterface>
WrapDocumentLoopbackPermissionFactory(
    scoped_refptr<DocumentLoopbackContext> context,
    std::unique_ptr<webrtc::LocalNetworkAccessPermissionFactoryInterface>
        native);

class PLATFORM_EXPORT P2PPortAllocator : public webrtc::BasicPortAllocator {
 public:
  struct Config {
    // Enable non-proxied UDP-based transport when set to true. When set to
    // false, it effectively disables all UDP traffic until UDP-supporting proxy
    // RETURN is available in the future.
    bool enable_nonproxied_udp = true;

    // Request binding to individual NICs. Whether multiple routes is allowed is
    // subject to the permission check on mic/camera. When specified as false or
    // the permission request is denied, it still uses the default local address
    // to generate a single local candidate. TODO(guoweis): Rename this to
    // |request_multiple_routes|.
    bool enable_multiple_routes = true;

    // Enable exposing the default local address when set to true. This is
    // only in effect when the |enable_multiple_routes| is false or the
    // permission check of mic/camera is denied.
    bool enable_default_local_candidate = true;
  };

  P2PPortAllocator(
      std::unique_ptr<webrtc::NetworkManager> network_manager,
      webrtc::PacketSocketFactory* socket_factory,
      const Config& config,
      std::unique_ptr<webrtc::LocalNetworkAccessPermissionFactoryInterface>
          lna_permission_factory,
      std::unique_ptr<webrtc::PacketSocketFactory> document_socket_factory =
          nullptr);
  P2PPortAllocator(const P2PPortAllocator&) = delete;
  P2PPortAllocator& operator=(const P2PPortAllocator&) = delete;
  ~P2PPortAllocator() override;

  // Will also initialize the network manager passed into the constructor.
  void Initialize() override;

 private:
  std::unique_ptr<webrtc::NetworkManager> network_manager_;
  std::unique_ptr<webrtc::PacketSocketFactory> document_socket_factory_;
  Config config_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_P2P_PORT_ALLOCATOR_H_
