package sa.kps.driver;

import android.webkit.CookieManager;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import javax.net.ssl.HttpsURLConnection;

/** Existing host-only work session and existing CSRF; no new credentials or backend. */
final class WorkApi {
    static final String ORIGIN = "https://order.kps.sa";
    static final class Denied extends Exception {}
    static final class Grant {
        final String user, shift, csrf, cookie;
        Grant(String user, String shift, String csrf, String cookie) {
            this.user=user; this.shift=shift; this.csrf=csrf; this.cookie=cookie;
        }
        boolean same(Grant other) { return other != null && user.equals(other.user) && shift.equals(other.shift) && cookie.equals(other.cookie); }
    }
    static JSONObject call(String method, String path, String cookie, String csrf, JSONObject body) throws Exception {
        if (!path.startsWith("/api/")) throw new Denied();
        HttpsURLConnection c=(HttpsURLConnection)new URL(ORIGIN+path).openConnection();
        c.setInstanceFollowRedirects(false); c.setConnectTimeout(7000); c.setReadTimeout(7000);
        c.setRequestMethod(method); c.setRequestProperty("Accept","application/json");
        c.setRequestProperty("X-KPS-Context","work"); c.setRequestProperty("Cookie",cookie);
        try {
            if (body != null) {
                c.setRequestProperty("Origin",ORIGIN); c.setRequestProperty("X-CSRF-Token",csrf);
                c.setRequestProperty("Content-Type","application/json");
                c.setRequestProperty("Idempotency-Key",java.util.UUID.randomUUID().toString());
                c.setDoOutput(true);
                try (java.io.OutputStream out=c.getOutputStream()) { out.write(body.toString().getBytes(StandardCharsets.UTF_8)); }
            }
            int status=c.getResponseCode();
            if (status==401 || status==403 || status==409 || (status>=300 && status<400)) throw new Denied();
            if (status!=200) throw new java.io.IOException("Request unavailable");
            try (InputStream in=c.getInputStream(); ByteArrayOutputStream out=new ByteArrayOutputStream()) {
                byte[] buffer=new byte[4096]; int n;
                while ((n=in.read(buffer))!=-1) { if (out.size()+n>1024*1024) throw new java.io.IOException("Response too large"); out.write(buffer,0,n); }
                return new JSONObject(out.toString(StandardCharsets.UTF_8.name()));
            }
        } finally { c.disconnect(); }
    }
    static Grant current() throws Exception {
        String cookie=CookieManager.getInstance().getCookie(ORIGIN);
        if (cookie==null || cookie.isEmpty()) throw new Denied();
        JSONObject session=call("GET","/api/session",cookie,null,null);
        JSONObject u=session.optJSONObject("user"); String csrf=session.optString("csrf_token","");
        if (u==null || csrf.isEmpty() || "null".equals(csrf) || !"driver".equals(u.optString("role")) || !u.optBoolean("active")) throw new Denied();
        JSONArray shifts=call("GET","/api/shifts/current",cookie,null,null).optJSONArray("shifts");
        if (shifts!=null) for (int i=0;i<shifts.length();i++) {
            JSONObject s=shifts.getJSONObject(i); boolean overlap=false;
            JSONArray branches=u.optJSONArray("branch_ids"), assigned=s.optJSONArray("branch_ids");
            if (branches!=null && assigned!=null) for(int a=0;a<branches.length();a++) for(int b=0;b<assigned.length();b++)
                if(branches.getString(a).equals(assigned.getString(b))) overlap=true;
            if (u.optString("brand_id").equals(s.optString("brand_id")) && !s.optString("id").isEmpty()
                && TrackingPolicy.allowed(u.optString("role"),u.optBoolean("active"),u.optString("id"),s.optString("user_id"),s.optString("kind"),s.optString("status"),overlap))
                return new Grant(u.getString("id"),s.getString("id"),csrf,cookie);
        }
        throw new Denied();
    }
}
