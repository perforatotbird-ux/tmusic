package app.tmusic.mobile

import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import java.net.URL
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

data class DiscoveredServer(
    val name: String,
    val url: String,
    val ip: String,
    val port: Int,
    val method: String
) {
    fun toJson(): JSONObject {
        return JSONObject().apply {
            put("name", name)
            put("url", url)
            put("ip", ip)
            put("port", port)
            put("method", method)
        }
    }
}

object LanServerScanner {
    private const val TAG = "LanServerScanner"
    private const val DISCOVERY_UDP_PORT = 8211
    private const val DISCOVERY_MAGIC_REQ = "TMUSIC_DISCOVER"
    private val DEFAULT_HTTP_PORTS = intArrayOf(8005, 8000)

    fun getLocalIpv4Address(context: Context): String? {
        try {
            val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            val wifiIp = wifiManager?.connectionInfo?.ipAddress
            if (wifiIp != null && wifiIp != 0) {
                val ipStr = String.format(
                    "%d.%d.%d.%d",
                    wifiIp and 0xff,
                    wifiIp shr 8 and 0xff,
                    wifiIp shr 16 and 0xff,
                    wifiIp shr 24 and 0xff
                )
                if (!ipStr.startsWith("0.") && !ipStr.startsWith("127.")) {
                    return ipStr
                }
            }
        } catch (e: Exception) {
            Log.d(TAG, "WifiManager IP resolution skipped: ${e.message}")
        }

        try {
            val interfaces = NetworkInterface.getNetworkInterfaces()
            while (interfaces.hasMoreElements()) {
                val networkInterface = interfaces.nextElement()
                if (networkInterface.isLoopback || !networkInterface.isUp) continue
                val addresses = networkInterface.inetAddresses
                while (addresses.hasMoreElements()) {
                    val addr = addresses.nextElement()
                    if (!addr.isLoopbackAddress && addr is Inet4Address) {
                        val host = addr.hostAddress ?: continue
                        if (host.startsWith("192.168.") || host.startsWith("10.") || host.startsWith("172.")) {
                            return host
                        }
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error enumerating network interfaces", e)
        }
        return null
    }

    fun startScan(
        context: Context,
        onServerFound: (DiscoveredServer) -> Unit,
        onComplete: (List<DiscoveredServer>) -> Unit
    ) {
        val executor = Executors.newCachedThreadPool()
        val foundSet = ConcurrentHashMap.newKeySet<String>()
        val foundList = mutableListOf<DiscoveredServer>()

        executor.execute {
            Log.i(TAG, "Starting LAN discovery (UDP + HTTP Subnet Probe)...")
            val localIp = getLocalIpv4Address(context)
            Log.i(TAG, "Local IP detected: $localIp")

            val scanPool = Executors.newFixedThreadPool(32)

            // 1. Run UDP broadcast discovery
            scanPool.execute {
                scanUdpBroadcast(localIp) { server ->
                    if (foundSet.add(server.url)) {
                        synchronized(foundList) { foundList.add(server) }
                        Log.i(TAG, "Discovered server via UDP: ${server.url}")
                        onServerFound(server)
                    }
                }
            }

            // 2. Run HTTP Subnet sweep if local IP is known
            if (localIp != null && localIp.contains(".")) {
                val prefix = localIp.substringBeforeLast(".") + "."
                for (host in 1..254) {
                    val targetIp = "$prefix$host"
                    for (port in DEFAULT_HTTP_PORTS) {
                        scanPool.execute {
                            val candidateUrl = "http://$targetIp:$port"
                            if (!foundSet.contains(candidateUrl)) {
                                probeHttpServer(targetIp, port) { server ->
                                    if (foundSet.add(server.url)) {
                                        synchronized(foundList) { foundList.add(server) }
                                        Log.i(TAG, "Discovered server via HTTP probe: ${server.url}")
                                        onServerFound(server)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Wait for scan pool to finish with a reasonable deadline
            scanPool.shutdown()
            try {
                scanPool.awaitTermination(3500, TimeUnit.MILLISECONDS)
            } catch (e: InterruptedException) {
                Log.w(TAG, "Scan interrupted")
            } finally {
                scanPool.shutdownNow()
            }

            Log.i(TAG, "LAN scan complete. Discovered ${foundList.size} server(s).")
            onComplete(ArrayList(foundList))
            executor.shutdown()
        }
    }

    private fun scanUdpBroadcast(localIp: String?, onFound: (DiscoveredServer) -> Unit) {
        var socket: DatagramSocket? = null
        try {
            socket = DatagramSocket()
            socket.broadcast = true
            socket.soTimeout = 1200

            val reqBytes = DISCOVERY_MAGIC_REQ.toByteArray(Charsets.UTF_8)

            // Send to global broadcast 255.255.255.255
            try {
                val globalAddr = InetAddress.getByName("255.255.255.255")
                socket.send(DatagramPacket(reqBytes, reqBytes.size, globalAddr, DISCOVERY_UDP_PORT))
            } catch (e: Exception) {
                Log.d(TAG, "Global UDP broadcast send failed: ${e.message}")
            }

            // Also send to subnet broadcast
            if (localIp != null && localIp.contains(".")) {
                try {
                    val subnetBroadcast = localIp.substringBeforeLast(".") + ".255"
                    val subnetAddr = InetAddress.getByName(subnetBroadcast)
                    socket.send(DatagramPacket(reqBytes, reqBytes.size, subnetAddr, DISCOVERY_UDP_PORT))
                } catch (e: Exception) {
                    Log.d(TAG, "Subnet UDP broadcast send failed: ${e.message}")
                }
            }

            val buffer = ByteArray(1024)
            val startTime = System.currentTimeMillis()
            while (System.currentTimeMillis() - startTime < 1200) {
                try {
                    val packet = DatagramPacket(buffer, buffer.size)
                    socket.receive(packet)
                    val senderIp = packet.address.hostAddress ?: continue
                    val text = String(packet.data, 0, packet.length, Charsets.UTF_8)
                    val json = JSONObject(text)
                    if (json.optString("app") == "tmusic") {
                        val port = json.optInt("port", 8005)
                        val name = json.optString("project", "T-Music Server")
                        val server = DiscoveredServer(
                            name = name,
                            url = "http://$senderIp:$port",
                            ip = senderIp,
                            port = port,
                            method = "udp"
                        )
                        onFound(server)
                    }
                } catch (e: SocketTimeoutException) {
                    break
                } catch (e: Exception) {
                    Log.d(TAG, "UDP packet parse error: ${e.message}")
                }
            }
        } catch (e: Exception) {
            Log.d(TAG, "UDP socket error: ${e.message}")
        } finally {
            socket?.close()
        }
    }

    private fun probeHttpServer(ip: String, port: Int, onFound: (DiscoveredServer) -> Unit) {
        var conn: HttpURLConnection? = null
        try {
            val url = URL("http://$ip:$port/health")
            conn = (url.openConnection() as HttpURLConnection).apply {
                connectTimeout = 300
                readTimeout = 400
                requestMethod = "GET"
                setRequestProperty("Accept", "application/json")
                setRequestProperty("User-Agent", "TMusicScanner/1.0")
                instanceFollowRedirects = false
            }

            val code = conn.responseCode
            if (code == 200) {
                val body = conn.inputStream.bufferedReader().use { it.readText() }
                val json = JSONObject(body)
                val status = json.optString("status")
                val project = json.optString("project", "T-Music Server")
                if (status == "ok" || project.contains("T-Music", ignoreCase = true)) {
                    val server = DiscoveredServer(
                        name = project,
                        url = "http://$ip:$port",
                        ip = ip,
                        port = port,
                        method = "http"
                    )
                    onFound(server)
                }
            }
        } catch (_: Exception) {
            // Unreachable host/port or connection refused, ignored during sweep
        } finally {
            conn?.disconnect()
        }
    }
}
