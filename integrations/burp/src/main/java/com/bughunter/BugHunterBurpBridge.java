package com.bughunter;

import burp.api.montoya.BurpExtension;
import burp.api.montoya.MontoyaApi;
import burp.api.montoya.proxy.ProxyHttpRequestResponse;
import burp.api.montoya.scanner.AuditConfiguration;
import burp.api.montoya.scanner.BuiltInAuditConfiguration;
import burp.api.montoya.scanner.audit.Audit;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.Locale;
import java.util.concurrent.ConcurrentLinkedDeque;
import java.util.concurrent.Executors;

/** Locally authenticated Burp bridge.
 *
 * Proxy history and issues are metadata-only. The optional active audit is
 * separately approved in Chat, limited to one existing in-scope GET/HEAD
 * history item, stoppable, and MUST NOT be used on unapproved destinations.
 * An active audit can send many modified requests and may change target data.
 */
public final class BugHunterBurpBridge implements BurpExtension {
    // Only names of recognized price/checkout inputs; never copy submitted amounts,
    // cookies, authorization headers or customer data into our local service.
    private static final Set<String> PRICE_NAMES = Set.of(
        "price", "unit_price", "unitprice", "product_price", "subtotal", "total",
        "total_amount", "amount", "discount", "discount_amount", "coupon",
        "coupon_code", "promo", "promo_code", "quantity", "qty", "shipping",
        "shipping_cost", "tax", "tax_amount", "currency", "cart_total",
        "grand_total", "credit", "balance", "gift_card", "gift_card_amount",
        "shipping_fee");
    private MontoyaApi api;
    private HttpServer server;
    private String token;
    private final Object repeaterLock = new Object();
    private final Object auditLock = new Object();
    private Audit activeAudit = null;
    private int auditHistoryId = -1;
    private final ConcurrentLinkedDeque<String> issueLedger = new ConcurrentLinkedDeque<>();

    @Override public void initialize(MontoyaApi api) {
        this.api = api;
        api.extension().setName("BugHunterClaudex Scoped Burp Bridge");
        try {
            Path folder=Path.of(System.getProperty("user.home"),".bughunter-claudex");
            // Bind first so loading a second extension cannot invalidate the live token.
            server=HttpServer.create(new InetSocketAddress(InetAddress.getByName("127.0.0.1"),18765),0);
            Files.createDirectories(folder);
            restrict(folder, "rwx------");
            Path secret=folder.resolve("burp-token");
            byte[] random=new byte[32]; new SecureRandom().nextBytes(random);
            token=Base64.getUrlEncoder().withoutPadding().encodeToString(random);
            Path temp=Files.createTempFile(folder,"burp-token-",".tmp",PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
            Files.writeString(temp,token,StandardCharsets.US_ASCII);
            Files.move(temp,secret,StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);
            restrict(secret,"rw-------");
            server.createContext("/health",this::handle);
            server.createContext("/history",this::handle);
            server.createContext("/repeater",this::handle);
            server.createContext("/issues",this::handle);
            server.createContext("/audit/start",this::handle);
            server.createContext("/audit/status",this::handle);
            server.createContext("/audit/stop",this::handle);
            try {
                // Burp Professional only. Read findings that Burp itself reports;
                // this extension does not launch or configure a scan.
                api.scanner().registerAuditIssueHandler(issue -> {
                    try {
                        String original=issue.baseUrl();
                        if(original==null || !api.scope().isInScope(original))return;
                        URI uri=new URI(original);
                        if(uri.getHost()==null || !("https".equalsIgnoreCase(uri.getScheme()) || "http".equalsIgnoreCase(uri.getScheme())))return;
                        String cleanUrl=new URI(uri.getScheme(),null,uri.getHost(),uri.getPort(),uri.getPath(),null,null).toString();
                        String record="{\"url\":\""+escape(cleanUrl)+"\",\"name\":\""+escape(issue.name())
                            +"\",\"severity\":\""+escape(issue.severity().toString())
                            +"\",\"confidence\":\""+escape(issue.confidence().toString())+"\"}";
                        issueLedger.addLast(record);
                        while(issueLedger.size()>200)issueLedger.pollFirst();
                    } catch(Exception ignored) { /* Ignore malformed/scoped-out issues. */ }
                });
            } catch(Exception unavailable) {
                api.logging().logToOutput("BugHunter: Burp Scanner issue feed unavailable on this edition.");
            }
            server.setExecutor(Executors.newSingleThreadExecutor(r->{Thread t=new Thread(r,"bughunter-history");t.setDaemon(true);return t;}));
            server.start();
            api.extension().registerUnloadingHandler(() -> { if(server!=null) server.stop(0); });
            api.logging().logToOutput("BugHunterClaudex read-only history bridge is listening on localhost:18765. Token is saved in your home directory.");
        } catch (IOException e) {
            api.logging().logToError("BugHunter bridge startup failed: " + e.getMessage());
            if(server!=null) server.stop(0);
        }
    }
    private static void restrict(Path path,String mode) {
        try{Files.setPosixFilePermissions(path,PosixFilePermissions.fromString(mode));}
        catch(UnsupportedOperationException | IOException ignored) { /* Unsupported on Windows */ }
    }
    private static String escape(String text) {
        if(text==null)return "";
        StringBuilder b=new StringBuilder();
        for(int i=0;i<text.length()&&i<600;i++) {
            char c=text.charAt(i);
            if(c=='"'||c=='\\')b.append('\\').append(c);
            else if(c<32)b.append(' ');
            else b.append(c);
        }
        return b.toString();
    }
    private static void send(HttpExchange ex,int status,String body)throws IOException {
        byte[] bytes=body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type","application/json; charset=utf-8");
        ex.getResponseHeaders().set("Cache-Control","no-store");
        ex.sendResponseHeaders(status,bytes.length);
        try(var out=ex.getResponseBody()){out.write(bytes);}
    }
    private void handle(HttpExchange ex)throws IOException {
        try{
            String endpoint=ex.getRequestURI().getPath();
            boolean post = endpoint.equals("/repeater") || endpoint.equals("/audit/start") || endpoint.equals("/audit/stop");
            if(post && !"POST".equals(ex.getRequestMethod())){send(ex,405,"{\"error\":\"POST required\"}");return;}
            if(!post && !"GET".equals(ex.getRequestMethod())){send(ex,405,"{\"error\":\"GET required\"}");return;}
            String key=ex.getRequestHeaders().getFirst("X-BugHunter-Token");
            if(key==null||token==null||!MessageDigest.isEqual(key.getBytes(StandardCharsets.UTF_8),token.getBytes(StandardCharsets.UTF_8))){send(ex,403,"{\"error\":\"Not authorized\"}");return;}
            if(endpoint.equals("/audit/status")) {
                synchronized(auditLock) {
                    if(activeAudit==null){send(ex,200,"{\"tracked\":false,\"history_id\":null}");return;}
                    String status=activeAudit.statusMessage();
                    send(ex,200,"{\"tracked\":true,\"history_id\":"+auditHistoryId
                        +",\"status\":\""+escape(status)+"\",\"requests\":"+activeAudit.requestCount()
                        +",\"errors\":"+activeAudit.errorCount()+",\"reported_issues\":"+activeAudit.issues().size()+"}");
                    return;
                }
            }
            if(endpoint.equals("/audit/stop")) {
                synchronized(auditLock) {
                    if(activeAudit!=null){activeAudit.delete();activeAudit=null;auditHistoryId=-1;}
                    send(ex,200,"{\"stopped\":true}");return;
                }
            }
            if(endpoint.equals("/audit/start")) {
                String requested=new String(ex.getRequestBody().readNBytes(24),StandardCharsets.US_ASCII).trim();
                if(!requested.matches("[1-9][0-9]{0,8}")){send(ex,400,"{\"error\":\"Invalid history ID\"}");return;}
                int number=Integer.parseInt(requested);
                synchronized(auditLock) {
                    if(activeAudit!=null){send(ex,409,"{\"error\":\"Stop the current audit before starting a new one\"}");return;}
                    for(var row:api.proxy().history()) {
                        if(row.id()!=number || !row.finalRequest().isInScope())continue;
                        var req=row.finalRequest();
                        URI uri=new URI(req.url());
                        String meth=req.method();
                        // Do not import a history request containing credentials or query values.
                        if(uri.getRawQuery()!=null || uri.getUserInfo()!=null || uri.getFragment()!=null
                            || !("GET".equals(meth) || "HEAD".equals(meth))
                            || !("http".equals(uri.getScheme()) || "https".equals(uri.getScheme()))) {
                            send(ex,422,"{\"error\":\"Only query-free in-scope GET/HEAD requests are supported\"}");return;
                        }
                        // Official Montoya API. Burp Pro runs the scan, not this HTTP server.
                        Audit audit=api.scanner().startAudit(AuditConfiguration.auditConfiguration(
                            BuiltInAuditConfiguration.LEGACY_ACTIVE_AUDIT_CHECKS));
                        audit.addRequest(req);
                        activeAudit=audit;
                        auditHistoryId=number;
                        send(ex,200,"{\"started\":true,\"history_id\":"+number
                            +",\"note\":\"Burp will send active audit requests; monitor or stop in Burp.\"}");return;
                    }
                    send(ex,404,"{\"error\":\"Burp history ID was not in scope\"}");return;
                }
            }
            if(endpoint.equals("/repeater")) {
                // Explicitly operator-approved, in-scope request from existing history.
                // Opening a Repeater tab does not send a network request.
                String requested=new String(ex.getRequestBody().readNBytes(24),StandardCharsets.US_ASCII).trim();
                if(!requested.matches("[1-9][0-9]{0,8}")){send(ex,400,"{\"error\":\"Invalid history ID\"}");return;}
                int number=Integer.parseInt(requested);
                synchronized(repeaterLock){
                    boolean opened=false;
                    for(var row:api.proxy().history()){
                        if(row.id()==number && row.finalRequest().isInScope()){
                            api.repeater().sendToRepeater(row.finalRequest(),"BugHunter review #"+number);
                            opened=true;break;
                        }
                    }
                    if(!opened){send(ex,404,"{\"error\":\"History ID unavailable or outside Burp scope\"}");return;}
                }
                send(ex,200,"{\"opened\":true,\"sent_request\":false,\"history_id\":"+number+"}");return;
            }
            if(ex.getRequestURI().getPath().equals("/health")){
                send(ex,200,"{\"service\":\"BugHunter Montoya\",\"connected\":true,\"active_audit_supported\":true}");return;
            }
            if(endpoint.equals("/issues")) {
                StringBuilder body=new StringBuilder("{\"items\":[");int count=0;
                for(String issue:issueLedger){
                    if(count++>0)body.append(',');
                    body.append(issue);
                }
                body.append("],\"source\":\"Burp Scanner issue notifications\"}");
                send(ex,200,body.toString());return;
            }
            if(!endpoint.equals("/history")){send(ex,404,"{\"error\":\"Unknown endpoint\"}");return;}
            List<ProxyHttpRequestResponse> history=api.proxy().history();
            StringBuilder b=new StringBuilder("{\"items\":[");int written=0;
            for(int i=history.size()-1;i>=0&&written<100;i--){
                try {
                    var entry=history.get(i);
                    var req=entry.finalRequest();
                    if(!req.isInScope())continue;
                    URI uri=new URI(req.url());
                    String host=uri.getHost();
                    if(host==null)continue;
                    if(written++>0)b.append(',');
                    String path=uri.getRawPath()==null?"/":uri.getRawPath();
                    String method=req.method();
                    int status=entry.hasResponse()?entry.response().statusCode():0;
                    b.append("{\"method\":\"").append(escape(method)).append("\",\"scheme\":\"").append(escape(uri.getScheme()))
                     .append("\",\"host\":\"").append(escape(host))
                     .append("\",\"path\":\"").append(escape(path))
                     .append("\",\"port\":").append(uri.getPort())
                     .append(",\"status\":").append(status).append(",\"burp_history_id\":").append(entry.id());
                    // Montoya parses URL, form and supported JSON parameters.
                    // Whitelist schema keys, discard EVERY value before leaving Burp.
                    TreeSet<String> pricingNames = new TreeSet<>();
                    try {
                        for (var param : req.parameters()) {
                            String name = param.name();
                            if (name == null) continue;
                            String clean = name.toLowerCase(Locale.ROOT);
                            if (PRICE_NAMES.contains(clean)) pricingNames.add(clean);
                            if (pricingNames.size() >= 24) break;
                        }
                    } catch (Exception ignored) { /* Not all content types have parsed parameters */ }
                    b.append(",\"pricing_fields\":[");
                    boolean firstName = true;
                    for (String name : pricingNames) {
                        if (!firstName) b.append(',');
                        b.append('\"').append(escape(name)).append('\"');
                        firstName = false;
                    }
                    b.append(']');
                    if (entry.hasResponse()) {
                        var response=entry.response();
                        b.append(",\"response_checks\":{")
                         .append("\"hsts\":").append(response.hasHeader("Strict-Transport-Security"))
                         .append(",\"csp\":").append(response.hasHeader("Content-Security-Policy"))
                         .append(",\"frame_protection\":").append(response.hasHeader("X-Frame-Options") || response.hasHeader("Content-Security-Policy"))
                         .append(",\"nosniff\":").append(response.hasHeader("X-Content-Type-Options"))
                         .append('}');
                    }
                    b.append('}');
                } catch (Exception ignored){ /* malformed/unusable record; never leak bodies */ }
            }
            b.append("],\"count\":").append(written).append(",\"source\":\"Burp Montoya proxy history\"}");
            send(ex,200,b.toString());
        } catch(Exception e){send(ex,500,"{\"error\":\"Burp history unavailable\"}");}
        finally {ex.close();}
    }
}
