// Copyright 2012 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/platform/p2p/port_allocator.h"

#include <stdint.h>

#include <bitset>
#include <cerrno>
#include <cstring>
#include <map>
#include <memory>
#include <span>
#include <utility>
#include <vector>

#include "base/check.h"
#include "base/compiler_specific.h"
#include "base/containers/span.h"
#include "base/feature_list.h"
#include "base/functional/bind.h"
#include "base/location.h"
#include "base/memory/raw_ptr.h"
#include "base/memory/weak_ptr.h"
#include "base/sequence_checker.h"
#include "base/task/single_thread_task_runner.h"
#include "third_party/blink/public/platform/platform.h"
#include "third_party/webrtc/api/async_dns_resolver.h"
#include "third_party/webrtc/api/local_network_access_permission.h"
#include "third_party/webrtc/api/packet_socket_factory.h"
#include "third_party/webrtc/rtc_base/async_packet_socket.h"
#include "third_party/webrtc/rtc_base/ip_address.h"
#include "third_party/webrtc/rtc_base/network/received_packet.h"
#include "third_party/webrtc/rtc_base/network/sent_packet.h"
#include "third_party/webrtc/system_wrappers/include/clock.h"
#include "third_party/webrtc_overrides/environment.h"

namespace blink {

namespace {

BASE_FEATURE(kDocumentLoopback,
             "ShardXWebRtcDocumentLoopback",
             base::FEATURE_ENABLED_BY_DEFAULT);

constexpr size_t kMaximumDocumentDatagram = 65507;
constexpr size_t kMaximumDocumentPendingBytes = 4 * 1024 * 1024;

bool IsDocumentAddress(const webrtc::SocketAddress& address) {
  return !address.IsUnresolvedIP() &&
         (address.ipaddr() == webrtc::GetLoopbackIP(AF_INET) ||
          address.ipaddr() == webrtc::GetLoopbackIP(AF_INET6));
}

bool IsMdnsName(const webrtc::SocketAddress& address) {
  std::string name = address.hostname();
  while (!name.empty() && name.back() == '.')
    name.pop_back();
  for (char& ch : name) {
    if (ch >= 'A' && ch <= 'Z')
      ch += 'a' - 'A';
  }
  return name == "local" || name.ends_with(".local");
}

// Never pre-resolve WebRTC names through the browser or system resolver.
// TURN/TCP and TLS already have a native resolution-failure path that passes
// the unresolved hostname to the proxy-capable TCP socket factory.
class DocumentDnsResolver final : public webrtc::AsyncDnsResolverInterface,
                                  public webrtc::AsyncDnsResolverResult {
 public:
  void Start(const webrtc::SocketAddress& address,
             absl::AnyInvocable<void()> callback) override {
    Start(address, AF_UNSPEC, std::move(callback));
  }

  void Start(const webrtc::SocketAddress&,
             int,
             absl::AnyInvocable<void()> callback) override {
    CHECK(!started_);
    started_ = true;
    // Preserve asynchronous resolver semantics, including cancellation on
    // delete.
    base::SingleThreadTaskRunner::GetCurrentDefault()->PostTask(
        FROM_HERE, base::BindOnce(
                       [](base::WeakPtr<DocumentDnsResolver> self,
                          absl::AnyInvocable<void()> callback) {
                         if (self)
                           callback();
                       },
                       weak_factory_.GetWeakPtr(), std::move(callback)));
  }

  const webrtc::AsyncDnsResolverResult& result() const override {
    return *this;
  }
  bool GetResolvedAddress(int, webrtc::SocketAddress*) const override {
    return false;
  }
  int GetError() const override { return EHOSTUNREACH; }

 private:
  bool started_ = false;
  base::WeakPtrFactory<DocumentDnsResolver> weak_factory_{this};
};

class DocumentPeerResolverFactory final
    : public webrtc::AsyncDnsResolverFactoryInterface {
 public:
  std::unique_ptr<webrtc::AsyncDnsResolverInterface> Create() override {
    return std::make_unique<DocumentDnsResolver>();
  }
  std::unique_ptr<webrtc::AsyncDnsResolverInterface> CreateAndResolve(
      const webrtc::SocketAddress& address,
      absl::AnyInvocable<void()> callback) override {
    auto resolver = Create();
    resolver->Start(address, std::move(callback));
    return resolver;
  }
  std::unique_ptr<webrtc::AsyncDnsResolverInterface> CreateAndResolve(
      const webrtc::SocketAddress& address,
      int family,
      absl::AnyInvocable<void()> callback) override {
    auto resolver = Create();
    resolver->Start(address, family, std::move(callback));
    return resolver;
  }
};

class DocumentNetworkManager final : public webrtc::NetworkManagerBase {
 public:
  explicit DocumentNetworkManager(
      scoped_refptr<DocumentLoopbackContext> context)
      : context_(std::move(context)),
        ipv4_("document-v4",
              "Document transport",
              webrtc::GetLoopbackIP(AF_INET),
              32,
              webrtc::ADAPTER_TYPE_UNKNOWN),
        ipv6_("document-v6",
              "Document transport",
              webrtc::GetLoopbackIP(AF_INET6),
              128,
              webrtc::ADAPTER_TYPE_UNKNOWN) {
    // These are virtual interfaces. No IpcNetworkManager is queried or started.
    ipv4_.AddIP(webrtc::GetLoopbackIP(AF_INET));
    ipv6_.AddIP(webrtc::GetLoopbackIP(AF_INET6));
    ipv4_.set_id(1);
    ipv6_.set_id(2);
    ipv4_.set_default_local_address_provider(this);
    ipv6_.set_default_local_address_provider(this);
    set_enumeration_permission(ENUMERATION_ALLOWED);
  }
  void StartUpdating() override {
    ++started_;
    base::SingleThreadTaskRunner::GetCurrentDefault()->PostTask(
        FROM_HERE, base::BindOnce(
                       [](base::WeakPtr<DocumentNetworkManager> self) {
                         if (self && self->started_)
                           self->NotifyNetworksChanged();
                       },
                       weak_factory_.GetWeakPtr()));
  }
  void StopUpdating() override {
    CHECK_GT(started_, 0);
    --started_;
  }
  std::vector<const webrtc::Network*> GetNetworks() const override {
    if (!context_->IsAlive())
      return {};
    return {&ipv4_, &ipv6_};
  }
  std::vector<const webrtc::Network*> GetAnyAddressNetworks() override {
    // Never supply an OS wildcard route, including allocator fallback paths.
    return GetNetworks();
  }
  bool GetDefaultLocalAddress(int family,
                              webrtc::IPAddress* address) const override {
    if (!context_->IsAlive() || (family != AF_INET && family != AF_INET6))
      return false;
    *address = webrtc::GetLoopbackIP(family);
    return true;
  }
  webrtc::MdnsResponderInterface* GetMdnsResponder() const override {
    return nullptr;
  }

 private:
  scoped_refptr<DocumentLoopbackContext> context_;
  webrtc::Network ipv4_;
  webrtc::Network ipv6_;
  unsigned started_ = 0;
  base::WeakPtrFactory<DocumentNetworkManager> weak_factory_{this};
};

class DocumentSocketFactory final : public webrtc::PacketSocketFactory {
 public:
  DocumentSocketFactory(scoped_refptr<DocumentLoopbackContext> context,
                        webrtc::PacketSocketFactory* native)
      : context_(std::move(context)), native_(native) {
    CHECK(native_);
  }
  std::unique_ptr<webrtc::AsyncPacketSocket> CreateUdpSocket(
      const webrtc::Environment& env,
      const webrtc::SocketAddress& address,
      uint16_t min_port,
      uint16_t max_port) override {
    // There is intentionally no delegation or OS-socket fallback for UDP.
    return context_->CreateSocket(env, address, min_port, max_port);
  }
  std::unique_ptr<webrtc::AsyncListenSocket> CreateServerTcpSocket(
      const webrtc::Environment&,
      const webrtc::SocketAddress&,
      uint16_t,
      uint16_t,
      int) override {
    return nullptr;
  }
  std::unique_ptr<webrtc::AsyncPacketSocket> CreateClientTcpSocket(
      const webrtc::Environment& env,
      const webrtc::SocketAddress&,
      const webrtc::SocketAddress& remote,
      const webrtc::PacketSocketTcpOptions& options) override {
    if (!context_->IsAlive() || IsMdnsName(remote) ||
        !context_->ClaimNativeTcpDestination(remote))
      return nullptr;
    // TURN/TCP and TLS retain the existing browser IPC/proxy implementation.
    // The virtual interface is not a real TCP source-address binding.
    const int family = remote.family() == AF_INET6 ? AF_INET6 : AF_INET;
    return native_->CreateClientTcpSocket(
        env, webrtc::SocketAddress(webrtc::GetAnyIP(family), 0), remote,
        options);
  }
  std::unique_ptr<webrtc::AsyncPacketSocket> CreateClientUdpSocket(
      const webrtc::Environment&,
      const webrtc::SocketAddress&,
      const webrtc::SocketAddress&,
      uint16_t,
      uint16_t,
      const webrtc::PacketSocketTcpOptions&) override {
    return nullptr;
  }
  std::unique_ptr<webrtc::AsyncDnsResolverInterface> CreateAsyncDnsResolver()
      override {
    return std::make_unique<DocumentDnsResolver>();
  }

 private:
  scoped_refptr<DocumentLoopbackContext> context_;
  raw_ptr<webrtc::PacketSocketFactory, DanglingUntriaged> native_;
};

class DocumentPermission final
    : public webrtc::LocalNetworkAccessPermissionInterface {
 public:
  DocumentPermission(
      scoped_refptr<DocumentLoopbackContext> context,
      std::unique_ptr<webrtc::LocalNetworkAccessPermissionInterface> native)
      : context_(std::move(context)), native_(std::move(native)) {}
  bool ShouldRequestPermission(const webrtc::SocketAddress& address) override {
    // This exemption is for a registered in-memory endpoint, not an arbitrary
    // loopback/private address. The socket still enforces the same ownership.
    return !context_->IsBoundEndpoint(address) &&
           native_->ShouldRequestPermission(address);
  }
  void RequestPermission(
      const webrtc::SocketAddress& address,
      absl::AnyInvocable<void(webrtc::LocalNetworkAccessPermissionStatus)>
          callback) override {
    if (!context_->IsBoundEndpoint(address)) {
      native_->RequestPermission(address, std::move(callback));
      return;
    }
    base::SingleThreadTaskRunner::GetCurrentDefault()->PostTask(
        FROM_HERE,
        base::BindOnce(
            [](base::WeakPtr<DocumentPermission> self,
               webrtc::SocketAddress address,
               absl::AnyInvocable<void(
                   webrtc::LocalNetworkAccessPermissionStatus)> callback) {
              if (self) {
                callback(
                    self->context_->IsBoundEndpoint(address)
                        ? webrtc::LocalNetworkAccessPermissionStatus::kGranted
                        : webrtc::LocalNetworkAccessPermissionStatus::kDenied);
              }
            },
            weak_factory_.GetWeakPtr(), address, std::move(callback)));
  }

 private:
  scoped_refptr<DocumentLoopbackContext> context_;
  std::unique_ptr<webrtc::LocalNetworkAccessPermissionInterface> native_;
  base::WeakPtrFactory<DocumentPermission> weak_factory_{this};
};

class DocumentPermissionFactory final
    : public webrtc::LocalNetworkAccessPermissionFactoryInterface {
 public:
  DocumentPermissionFactory(
      scoped_refptr<DocumentLoopbackContext> context,
      std::unique_ptr<webrtc::LocalNetworkAccessPermissionFactoryInterface>
          native)
      : context_(std::move(context)), native_(std::move(native)) {}
  std::unique_ptr<webrtc::LocalNetworkAccessPermissionInterface> Create()
      override {
    return std::make_unique<DocumentPermission>(context_, native_->Create());
  }

 private:
  scoped_refptr<DocumentLoopbackContext> context_;
  std::unique_ptr<webrtc::LocalNetworkAccessPermissionFactoryInterface> native_;
};

}  // namespace

// All State operations run on the WebRTC network thread. The only object shared
// with the document thread is the atomic revocation bit in the owning context.
class DocumentLoopbackContext::State {
 public:
  class Socket final : public webrtc::AsyncPacketSocket {
   public:
    Socket(scoped_refptr<DocumentLoopbackContext> owner,
           const webrtc::Environment& env,
           webrtc::SocketAddress local)
        : owner_(std::move(owner)), env_(env), local_(std::move(local)) {}
    ~Socket() override { Close(); }
    webrtc::SocketAddress GetLocalAddress() const override { return local_; }
    webrtc::SocketAddress GetRemoteAddress() const override { return {}; }
    webrtc::AsyncPacketSocket::State GetState() const override {
      return closed_ ? STATE_CLOSED : STATE_BOUND;
    }
    int Send(const void*,
             size_t,
             const webrtc::AsyncSocketPacketOptions&) override {
      return Fail(ENOTCONN);
    }
    int SendTo(const void* data,
               size_t size,
               const webrtc::SocketAddress& destination,
               const webrtc::AsyncSocketPacketOptions& options) override {
      if (closed_ || !owner_->IsAlive())
        return Fail(EBADF);
      if (size > kMaximumDocumentDatagram || (!data && size))
        return Fail(EMSGSIZE);
      auto receiver = owner_->state_->Find(destination);
      if (!receiver)
        return Fail(EHOSTUNREACH);
      if (size >
          kMaximumDocumentPendingBytes - owner_->state_->pending_bytes_) {
        owner_->state_->blocked_[{local_.family(), local_.port()}] =
            weak_factory_.GetWeakPtr();
        return Fail(EWOULDBLOCK);
      }
      std::vector<uint8_t> packet(size);
      if (size) {
        // AsyncPacketSocket's API supplies the buffer extent; bound it before
        // copying and never retain the caller's pointer across the posted task.
        base::span(packet).copy_from(UNSAFE_BUFFERS(
            base::span(static_cast<const uint8_t*>(data), size)));
      }
      owner_->state_->pending_bytes_ += size;
      base::SingleThreadTaskRunner::GetCurrentDefault()->PostTask(
          FROM_HERE,
          base::BindOnce(
              [](scoped_refptr<DocumentLoopbackContext> owner,
                 base::WeakPtr<Socket> sender, base::WeakPtr<Socket> receiver,
                 webrtc::SocketAddress source, std::vector<uint8_t> packet) {
                owner->state_->pending_bytes_ -= packet.size();
                if (owner->IsAlive() && sender && receiver)
                  receiver->Receive(source, packet);
                auto blocked = std::move(owner->state_->blocked_);
                owner->state_->blocked_.clear();
                for (const auto& [endpoint, waiting] : blocked) {
                  if (owner->IsAlive() && waiting) {
                    waiting->SetError(0);
                    waiting->NotifyReadyToSend(waiting.get());
                  }
                }
              },
              owner_, weak_factory_.GetWeakPtr(), receiver, local_,
              std::move(packet)));
      webrtc::SentPacketInfo sent(options.packet_id,
                                  env_.clock().CurrentTime().ms(),
                                  options.info_signaled_after_sent);
      webrtc::CopySocketInformationToPacketInfo(size, *this, &sent.info);
      NotifySentPacket(this, sent);
      return static_cast<int>(size);
    }
    int Close() override {
      if (!closed_) {
        closed_ = true;
        weak_factory_.InvalidateWeakPtrs();
        owner_->state_->endpoints_.erase({local_.family(), local_.port()});
        owner_->state_->blocked_.erase({local_.family(), local_.port()});
      }
      return 0;
    }
    int GetOption(webrtc::Socket::Option option, int* value) override {
      if (!value)
        return Fail(EINVAL);
      const auto it = options_.find(option);
      if (it == options_.end())
        return Fail(EINVAL);
      *value = it->second;
      return 0;
    }
    int SetOption(webrtc::Socket::Option option, int value) override {
      options_[option] = value;
      return 0;
    }
    int GetError() const override { return error_; }
    void SetError(int error) override { error_ = error; }
    base::WeakPtr<Socket> GetWeakPtr() { return weak_factory_.GetWeakPtr(); }

   private:
    int Fail(int error) {
      error_ = error;
      return -1;
    }
    void Receive(const webrtc::SocketAddress& source,
                 const std::vector<uint8_t>& bytes) {
      const webrtc::ReceivedIpPacket packet(std::span<const uint8_t>(bytes),
                                            source, env_.clock().CurrentTime());
      NotifyPacketReceived(packet);
    }
    scoped_refptr<DocumentLoopbackContext> owner_;
    webrtc::Environment env_;
    webrtc::SocketAddress local_;
    std::map<webrtc::Socket::Option, int> options_;
    bool closed_ = false;
    int error_ = 0;
    base::WeakPtrFactory<Socket> weak_factory_{this};
  };

  base::WeakPtr<Socket> Find(const webrtc::SocketAddress& address) const {
    DCHECK_CALLED_ON_VALID_SEQUENCE(sequence_checker_);
    if (!IsDocumentAddress(address))
      return {};
    const auto it = endpoints_.find({address.family(), address.port()});
    return it == endpoints_.end() ? base::WeakPtr<Socket>() : it->second;
  }

  uint16_t Allocate(int family, uint16_t min_port, uint16_t max_port) {
    DCHECK_CALLED_ON_VALID_SEQUENCE(sequence_checker_);
    const uint32_t first = min_port ? min_port : 49152;
    const uint32_t last = max_port ? max_port : 65535;
    auto& used = family == AF_INET ? ipv4_used_ : ipv6_used_;
    const auto& native = family == AF_INET ? ipv4_native_ : ipv6_native_;
    for (uint32_t port = first; port <= last; ++port) {
      if (!used.test(port) && !native.test(port)) {
        used.set(port);
        return static_cast<uint16_t>(port);
      }
    }
    return 0;
  }

  bool ClaimNativeTcpDestination(const webrtc::SocketAddress& address) {
    DCHECK_CALLED_ON_VALID_SEQUENCE(sequence_checker_);
    const uint16_t port = address.port();
    if (!port)
      return false;
    if (address.IsUnresolvedIP()) {
      // Proxy-side resolution can select either address family, including a
      // localhost alias. Do not let a name escape an existing virtual port's
      // ownership; reserve both families when native TCP claimed it first.
      if (ipv4_used_.test(port) || ipv6_used_.test(port))
        return false;
      ipv4_native_.set(port);
      ipv6_native_.set(port);
      return true;
    }
    if (!IsDocumentAddress(address))
      return true;
    const auto& used = address.family() == AF_INET ? ipv4_used_ : ipv6_used_;
    if (used.test(address.port()))
      return false;
    auto& native = address.family() == AF_INET ? ipv4_native_ : ipv6_native_;
    native.set(address.port());
    return true;
  }

  std::map<std::pair<int, int>, base::WeakPtr<Socket>> endpoints_;
  std::map<std::pair<int, int>, base::WeakPtr<Socket>> blocked_;
  // Never reuse an endpoint within a document. Delayed packets cannot bind to a
  // replacement socket even when a caller closes and recreates PeerConnections.
  std::bitset<65536> ipv4_used_;
  std::bitset<65536> ipv6_used_;
  std::bitset<65536> ipv4_native_;
  std::bitset<65536> ipv6_native_;
  size_t pending_bytes_ = 0;
  SEQUENCE_CHECKER(sequence_checker_);
};

DocumentLoopbackContext::DocumentLoopbackContext() = default;
DocumentLoopbackContext::~DocumentLoopbackContext() = default;
void DocumentLoopbackContext::Invalidate() {
  alive_.store(false, std::memory_order_release);
}
bool DocumentLoopbackContext::IsAlive() const {
  return alive_.load(std::memory_order_acquire);
}
bool DocumentLoopbackContext::IsBoundEndpoint(
    const webrtc::SocketAddress& address) const {
  return IsAlive() && state_ && state_->Find(address);
}
bool DocumentLoopbackContext::ClaimNativeTcpDestination(
    const webrtc::SocketAddress& address) {
  if (!IsAlive())
    return false;
  if (!state_)
    state_ = std::make_unique<State>();
  return state_->ClaimNativeTcpDestination(address);
}
std::unique_ptr<webrtc::AsyncPacketSocket>
DocumentLoopbackContext::CreateSocket(const webrtc::Environment& env,
                                      const webrtc::SocketAddress& address,
                                      uint16_t min_port,
                                      uint16_t max_port) {
  if (!IsAlive() || !IsDocumentAddress(address))
    return nullptr;
  if (!state_)
    state_ = std::make_unique<State>();
  const uint16_t port = state_->Allocate(address.family(), min_port, max_port);
  if (!port)
    return nullptr;
  auto socket = std::make_unique<State::Socket>(
      base::WrapRefCounted(this), env,
      webrtc::SocketAddress(address.ipaddr(), port));
  state_->endpoints_.emplace(std::make_pair(address.family(), port),
                             socket->GetWeakPtr());
  return socket;
}

bool DocumentLoopbackEnabled() {
  return base::FeatureList::IsEnabled(kDocumentLoopback);
}
std::unique_ptr<webrtc::NetworkManager> CreateDocumentLoopbackNetworkManager(
    scoped_refptr<DocumentLoopbackContext> context) {
  return std::make_unique<DocumentNetworkManager>(std::move(context));
}
std::unique_ptr<webrtc::PacketSocketFactory>
CreateDocumentLoopbackSocketFactory(
    scoped_refptr<DocumentLoopbackContext> context,
    webrtc::PacketSocketFactory* native_factory) {
  return std::make_unique<DocumentSocketFactory>(std::move(context),
                                                 native_factory);
}
std::unique_ptr<webrtc::AsyncDnsResolverFactoryInterface>
CreateDocumentLoopbackPeerResolverFactory() {
  return std::make_unique<DocumentPeerResolverFactory>();
}
std::unique_ptr<webrtc::LocalNetworkAccessPermissionFactoryInterface>
WrapDocumentLoopbackPermissionFactory(
    scoped_refptr<DocumentLoopbackContext> context,
    std::unique_ptr<webrtc::LocalNetworkAccessPermissionFactoryInterface>
        native) {
  return std::make_unique<DocumentPermissionFactory>(std::move(context),
                                                     std::move(native));
}

P2PPortAllocator::P2PPortAllocator(
    std::unique_ptr<webrtc::NetworkManager> network_manager,
    webrtc::PacketSocketFactory* socket_factory,
    const Config& config,
    std::unique_ptr<webrtc::LocalNetworkAccessPermissionFactoryInterface>
        lna_permission_factory,
    std::unique_ptr<webrtc::PacketSocketFactory> document_socket_factory)
    : webrtc::BasicPortAllocator(WebRtcEnvironment(),
                                 network_manager.get(),
                                 document_socket_factory
                                     ? document_socket_factory.get()
                                     : socket_factory,
                                 /*turn_customizer=*/nullptr,
                                 /*relay_port_factory=*/nullptr,
                                 std::move(lna_permission_factory)),
      network_manager_(std::move(network_manager)),
      document_socket_factory_(std::move(document_socket_factory)),
      config_(config) {
  DCHECK(network_manager_);
  DCHECK(socket_factory);
  uint32_t flags = 0;
  if (!config_.enable_multiple_routes) {
    flags |= webrtc::PORTALLOCATOR_DISABLE_ADAPTER_ENUMERATION;
  }
  if (!config_.enable_default_local_candidate) {
    flags |= webrtc::PORTALLOCATOR_DISABLE_DEFAULT_LOCAL_CANDIDATE;
  }
  if (!config_.enable_nonproxied_udp) {
    flags |= webrtc::PORTALLOCATOR_DISABLE_UDP |
             webrtc::PORTALLOCATOR_DISABLE_STUN |
             webrtc::PORTALLOCATOR_DISABLE_UDP_RELAY;
  }
  if (document_socket_factory_) {
    CHECK(!config_.enable_nonproxied_udp);
    // The only host datagrams use the document-owned memory factory. Native
    // STUN, UDP relay and host TCP remain disabled. TURN/TCP is unchanged.
    flags = webrtc::PORTALLOCATOR_DISABLE_STUN |
            webrtc::PORTALLOCATOR_DISABLE_UDP_RELAY |
            webrtc::PORTALLOCATOR_DISABLE_TCP;
    if (!(candidate_filter() & webrtc::CF_HOST))
      flags |= webrtc::PORTALLOCATOR_DISABLE_UDP;
    SubscribeCandidateFilterChanged(
        &document_socket_factory_, [this](uint32_t, uint32_t filter) {
          const uint32_t current = this->flags();
          set_flags(filter & webrtc::CF_HOST
                        ? current & ~webrtc::PORTALLOCATOR_DISABLE_UDP
                        : current | webrtc::PORTALLOCATOR_DISABLE_UDP);
        });
  }
  set_flags(flags);
  set_allow_tcp_listen(false);
}

P2PPortAllocator::~P2PPortAllocator() {
  if (document_socket_factory_) {
    UnsubscribeCandidateFilterChanged(&document_socket_factory_);
    DiscardCandidatePool();
  }
}

void P2PPortAllocator::Initialize() {
  BasicPortAllocator::Initialize();
  network_manager_->Initialize();
}

}  // namespace blink
