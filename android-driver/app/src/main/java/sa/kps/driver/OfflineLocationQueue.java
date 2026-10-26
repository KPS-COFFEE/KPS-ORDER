package sa.kps.driver;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** App-private, encrypted, atomic queue. Neither credentials nor unencrypted coordinates persist.
 * The session digest is binding metadata, never an authentication credential.
 */
final class OfflineLocationQueue {
    private static final String KEY="kps-shift-location-queue-v1";
    private final AtomicFile file;
    private String scope="";
    private LocationQueueState state=new LocationQueueState(0);
    OfflineLocationQueue(Context context) {
        file=new AtomicFile(new File(context.getNoBackupFilesDir(),"shift-location.queue"));
    }
    static String scope(WorkApi.Grant grant)throws Exception {
        byte[] bytes=MessageDigest.getInstance("SHA-256").digest((grant.user+"\n"+grant.shift+"\n"+grant.cookie).getBytes(StandardCharsets.UTF_8));
        StringBuilder value=new StringBuilder();for(byte b:bytes)value.append(String.format(java.util.Locale.ROOT,"%02x",b&255));return value.toString();
    }
    private static SecretKey key()throws Exception {
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        if(store.containsAlias(KEY))return (SecretKey)store.getKey(KEY,null);
        KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEY,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }
    synchronized void bind(WorkApi.Grant grant)throws Exception {
        String next=scope(grant);state=new LocationQueueState(0);
        // AtomicFile.openRead also restores a backup after interrupted rename.
        if(file.getBaseFile().exists() || new File(file.getBaseFile().getPath()+".bak").exists()) {
            try {
                byte[] data=file.readFully();
                if(data.length<29||data.length>2*1024*1024)throw new IOException("Invalid queue");
                Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Arrays.copyOfRange(data,0,12)));
                JSONObject saved=new JSONObject(new String(cipher.doFinal(Arrays.copyOfRange(data,12,data.length)),StandardCharsets.UTF_8));
                if(LocationQueuePolicy.sameScope(next,saved.getString("scope"))) {
                    JSONArray points=saved.getJSONArray("points");
                    if(points.length()>LocationQueuePolicy.MAX_POINTS)throw new IOException("Queue overflow");
                    state=new LocationQueueState(saved.optInt("dropped",0));
                    for(int i=0;i<points.length();i++){
                        JSONObject p=points.getJSONObject(i);
                        state.append(new LocationQueueState.Point(p.getString("id"),Instant.parse(p.getString("captured_at")).toEpochMilli(),p.getDouble("lat"),p.getDouble("lng"),p.getDouble("accuracy_m")),System.currentTimeMillis());
                    }
                } else file.delete();
            } catch(Exception invalid) {file.delete();state=new LocationQueueState(1);}
        }
        scope=next;state.prune(System.currentTimeMillis());persist();
    }
    synchronized void add(WorkApi.Grant grant,long captured,double lat,double lng,double accuracy)throws Exception {
        if(!LocationQueuePolicy.sameScope(scope,scope(grant)))throw new WorkApi.Denied();
        state.append(new LocationQueueState.Point(UUID.randomUUID().toString(),captured,lat,lng,accuracy),System.currentTimeMillis());
        persist();
    }
    private JSONArray encode(List<LocationQueueState.Point> points)throws Exception {
        JSONArray result=new JSONArray();
        for(LocationQueueState.Point p:points)result.put(new JSONObject().put("id",p.id).put("captured_at",Instant.ofEpochMilli(p.capturedAt).toString())
            .put("lat",p.lat).put("lng",p.lng).put("accuracy_m",p.accuracy));
        return result;
    }
    synchronized JSONArray batch()throws Exception {
        // Leave one minute for transport before the server's 24-hour retention boundary.
        List<LocationQueueState.Point> batch=state.batch(System.currentTimeMillis()+60000);persist();return encode(batch);
    }
    synchronized void acknowledge(JSONArray acknowledged)throws Exception {
        Set<String> accepted=new HashSet<>();for(int i=0;i<acknowledged.length();i++)accepted.add(acknowledged.getString(i));
        state.acknowledge(accepted);persist();
    }
    synchronized int size(){return state.size();}
    synchronized int dropped(){return state.dropped();}
    synchronized void clear(){scope="";state=new LocationQueueState(0);file.delete();}
    private void persist()throws Exception {
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());
        byte[] encrypted=cipher.doFinal(new JSONObject().put("scope",scope).put("points",encode(state.snapshot())).put("dropped",state.dropped()).toString().getBytes(StandardCharsets.UTF_8));
        FileOutputStream out=null;
        try{out=file.startWrite();out.write(cipher.getIV());out.write(encrypted);file.finishWrite(out);}
        catch(IOException failure){if(out!=null)file.failWrite(out);throw failure;}
    }
}
