// Bounded fixtures: only explicitly owned TCP targets are forwarded. UDP
// ASSOCIATE, authentication and all other destinations are rejected.
import dgram from 'node:dgram';
import net from 'node:net';
import os from 'node:os';

const locals = new Set(
    Object.values(os.networkInterfaces()).flat().filter(Boolean).map(x => x.address.split('%')[0]));
locals.add('127.0.0.1');
locals.add('::1');

function reader(socket) {
  let buffered = Buffer.alloc(0), pending = null, failure = null;
  function drain() {
    if (pending && buffered.length >= pending.count) {
      const task = pending;
      pending = null;
      const value = buffered.subarray(0, task.count);
      buffered = buffered.subarray(task.count);
      task.resolve(value);
    }
  }
  const onData = chunk => {
    buffered = Buffer.concat([buffered, chunk]);
    drain();
  };
  const onError = error => {
    failure = error;
    pending?.reject(error);
    pending = null;
  };
  const onClose = () => onError(Error('Owned socket closed'));
  socket.on('data', onData);
  socket.on('error', onError);
  socket.on('close', onClose);
  return {
    read(count) {
      if (failure)
        return Promise.reject(failure);
      if (pending)
        throw Error('Concurrent fixture reads');
      return new Promise((resolve, reject) => {
        pending = {count, resolve, reject};
        drain();
      });
    },
    release() {
      socket.off('data', onData);
      socket.off('error', onError);
      socket.off('close', onClose);
      return buffered;
    },
  };
}

export async function controlledSocks(targets = new Map()) {
  const sockets = new Set(), events = [];
  const server = net.createServer(async client => {
    if (!locals.has(client.remoteAddress?.replace(/^::ffff:/, ''))) {
      client.destroy();
      return;
    }
    sockets.add(client);
    client.on('error', () => {});
    client.once('close', () => sockets.delete(client));
    client.setTimeout(20000, () => client.destroy());
    const input = reader(client);
    try {
      const hello = await input.read(2), methods = await input.read(hello[1]);
      if (hello[0] !== 5 || !methods.includes(0)) {
        client.end(Buffer.from([5, 255]));
        return;
      }
      client.write(Buffer.from([5, 0]));
      const header = await input.read(4);
      let host;
      if (header[3] === 1)
        host = [...await input.read(4)].join('.');
      else if (header[3] === 3)
        host = (await input.read((await input.read(1))[0])).toString('ascii');
      else {
        client.end(Buffer.from([5, 8, 0, 1, 0, 0, 0, 0, 0, 0]));
        return;
      }
      const port = (await input.read(2)).readUInt16BE();
      const target = header[1] === 1 ? targets.get(host + ':' + port) : undefined;
      events.push({time: Date.now(), command: header[1], host, port, accepted: !!target});
      if (!target) {
        client.end(Buffer.from([5, 7, 0, 1, 0, 0, 0, 0, 0, 0]));
        return;
      }
      if (target.host !== '127.0.0.1' || !Number.isInteger(target.port) || target.port < 1 ||
          target.port > 65535)
        throw Error('Fixture map must name an owned loopback server');
      const tail = input.release(), upstream = net.connect(target.port, target.host);
      sockets.add(upstream);
      upstream.on('error', () => client.destroy());
      let sent = tail.length, received = 0;
      client.on('data', data => sent += data.length);
      upstream.on('data', data => received += data.length);
      upstream.once('connect', () => {
        client.setTimeout(0);
        client.write(Buffer.from([5, 0, 0, 1, 127, 0, 0, 1, 0, 0]));
        if (tail.length)
          upstream.write(tail);
        client.pipe(upstream);
        upstream.pipe(client);
      });
      client.once('close', () => upstream.destroy());
      upstream.once('close', () => {
        sockets.delete(upstream);
        client.destroy();
        events.push({time: Date.now(), closed: true, host, port, sent, received});
      });
    } catch (error) {
      events.push({error: String(error.message)});
      client.destroy();
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return {
    url: 'socks5://127.0.0.1:' + server.address().port,
    events,
    async close() {
      for (const socket of sockets)
        socket.destroy();
      await new Promise(resolve => server.close(resolve));
    },
  };
}

export async function stunCanary() {
  const sockets = [], targets = [], events = [];
  const addresses = [
    '127.0.0.1', '::1',
    ...[...locals].filter(ip => /^(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)/.test(ip))
  ];
  for (const host of new Set(addresses)) {
    const socket = dgram.createSocket(host.includes(':') ? 'udp6' : 'udp4');
    socket.on('message', (message, remote) => {
      if (!locals.has(remote.address.split('%')[0]) || message.length < 20 ||
          message.readUInt32BE(4) !== 0x2112a442)
        return;
      events.push({
        host,
        time: Date.now(),
        bytes: message.length,
        source: remote.address,
        port: remote.port
      });
      if (remote.family === 'IPv4' && message.readUInt16BE(0) === 1) {
        const response = Buffer.alloc(32);
        response.writeUInt16BE(0x0101);
        response.writeUInt16BE(12, 2);
        message.copy(response, 4, 4, 20);
        response.writeUInt16BE(0x0020, 20);
        response.writeUInt16BE(8, 22);
        response[25] = 1;
        response.writeUInt16BE(remote.port ^ 0x2112, 26);
        remote.address.split('.').forEach(
            (part, i) => response[28 + i] = Number(part) ^ message[4 + i]);
        socket.send(response, remote.port, remote.address);
      }
    });
    try {
      await new Promise((resolve, reject) => {
        socket.once('error', reject);
        socket.bind(0, host, resolve);
      });
      socket.on('error', error => events.push({host, error: error.code}));
      sockets.push(socket);
      targets.push({host, port: socket.address().port});
    } catch (error) {
      socket.close();
      events.push({host, bindError: error.code});
    }
  }
  if (!targets.some(t => t.host === '127.0.0.1'))
    throw Error('Loopback canary unavailable');
  return {
    targets,
    events,
    close() {
      for (const socket of sockets)
        socket.close();
    }
  };
}
