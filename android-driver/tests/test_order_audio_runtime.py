"""Run the actual OrderAudio refresh code with deterministic device/network boundaries.

This does not claim physical playback acceptance. The production sound validators,
fallback waveform generator and refresh class are compiled unchanged.
"""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = ROOT / "app/src/main/java/sa/kps/driver"
STUBS = {
"android/Manifest.java": "package android; public class Manifest { public static class permission { public static final String POST_NOTIFICATIONS=\"notify\"; } }",
"android/content/pm/PackageManager.java": "package android.content.pm; public class PackageManager { public static final int PERMISSION_GRANTED=0; }",
"android/content/Context.java": "package android.content; public class Context { private final java.io.File cache; public Context(java.io.File f){cache=f;} public java.io.File getCacheDir(){return cache;} public <T>T getSystemService(Class<T> c){return null;} public int checkSelfPermission(String p){return 0;} }",
"android/app/NotificationChannel.java": "package android.app; public class NotificationChannel { public int getImportance(){return 3;} public Object getSound(){return new Object();} }",
"android/app/NotificationManager.java": "package android.app; public class NotificationManager { public static final int INTERRUPTION_FILTER_ALL=1,IMPORTANCE_DEFAULT=3; public NotificationChannel getNotificationChannel(String s){return null;} public boolean areNotificationsEnabled(){return true;} public int getCurrentInterruptionFilter(){return 1;} }",
"android/os/Handler.java": "package android.os; public class Handler { public Handler(Looper l){} public void post(Runnable r){} public void postDelayed(Runnable r,long t){} public void removeCallbacks(Runnable r){} }",
"android/os/Looper.java": "package android.os; public class Looper { public static Looper getMainLooper(){return new Looper();} }",
"android/os/SystemClock.java": "package android.os; public class SystemClock { public static long now; public static long elapsedRealtime(){return now;} }",
"android/os/Build.java": "package android.os; public class Build { public static class VERSION { public static final int SDK_INT=35; } }",
"android/media/AudioAttributes.java": "package android.media; public class AudioAttributes { public static final int USAGE_NOTIFICATION=5,CONTENT_TYPE_SONIFICATION=4; public static class Builder { public Builder setUsage(int n){return this;} public Builder setContentType(int n){return this;} public AudioAttributes build(){return new AudioAttributes();} } }",
"android/media/AudioFocusRequest.java": "package android.media; public class AudioFocusRequest { public static class Builder { public Builder(int n){} public Builder setAudioAttributes(AudioAttributes a){return this;} public Builder setOnAudioFocusChangeListener(java.util.function.IntConsumer f,android.os.Handler h){return this;} public AudioFocusRequest build(){return new AudioFocusRequest();} } }",
"android/media/AudioManager.java": "package android.media; public class AudioManager { public static final int RINGER_MODE_NORMAL=2,STREAM_NOTIFICATION=5,AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK=3,AUDIOFOCUS_REQUEST_GRANTED=1; public int getRingerMode(){return 2;} public int getStreamVolume(int n){return 5;} public int requestAudioFocus(AudioFocusRequest f){return 1;} public void abandonAudioFocusRequest(AudioFocusRequest f){} }",
"android/media/MediaPlayer.java": "package android.media; public class MediaPlayer { public interface OnPreparedListener {void run(MediaPlayer p);} public interface OnCompletionListener {void run(MediaPlayer p);} public interface OnErrorListener {boolean run(MediaPlayer p,int a,int b);} public void setAudioAttributes(AudioAttributes a){} public void setDataSource(String s){} public void setOnPreparedListener(OnPreparedListener f){} public void setOnCompletionListener(OnCompletionListener f){} public void setOnErrorListener(OnErrorListener f){} public void start(){} public void prepareAsync(){} public void release(){} }",
"org/json/JSONObject.java": "package org.json; public class JSONObject { private final java.util.Map<String,Object> data=new java.util.HashMap<>(); public JSONObject put(String k,Object v){data.put(k,v);return this;} public JSONObject getJSONObject(String k){return (JSONObject)data.get(k);} public String getString(String k){return (String)data.get(k);} public int getInt(String k){return (Integer)data.get(k);} }",
"sa/kps/driver/ShiftLocationService.java": "package sa.kps.driver; public class ShiftLocationService { static final String ORDERS=\"orders\"; }",
"sa/kps/driver/WorkApi.java": r'''package sa.kps.driver;
import java.util.*; import org.json.JSONObject;
final class WorkApi {
 static final class Denied extends Exception {}
 static final class HttpFailure extends java.io.IOException { final int status; HttpFailure(int status){this.status=status;} }
 static final class Grant { final Set<String> brands=new HashSet<>(); final String cookie="test-only"; }
 static final Map<String,Integer> counts=new HashMap<>();
 static final Map<String,Exception> failures=new HashMap<>();
 static boolean downloadMissing,wrongBrand;
 static JSONObject call(String method,String path,String cookie,String csrf,JSONObject body)throws Exception {
  String brand=path.substring(path.indexOf("brand_id=")+9); counts.merge(brand,1,Integer::sum);
  Exception failure=failures.get(brand);if(failure!=null)throw failure;
  byte[] bytes=ApprovedOrderRing.bytes(brand);String hash=SoundPolicy.digest(bytes);
  return new JSONObject().put("sound",new JSONObject().put("brand_id",wrongBrand?"other":brand).put("sha256",hash).put("bytes",bytes.length).put("path","/api/operations/driver-order-sound/brands/"+brand+"/audio/"+hash));
 }
 static byte[] soundBytes(String path,String cookie)throws Exception {
  if(downloadMissing)throw new HttpFailure(404);
  String brand=path.substring(path.indexOf("/brands/")+8,path.indexOf("/audio/"));return ApprovedOrderRing.bytes(brand);
 }
}
''',
}

HARNESS = r'''package sa.kps.driver;
import java.util.*;import java.io.*;import java.lang.reflect.*;import android.os.SystemClock;
public class OrderAudioRefreshHarness {
 static int cases;
 static void check(boolean v,String why){cases++;if(!v)throw new AssertionError(why);}
 @SuppressWarnings("unchecked") static Map<String,File> selected(OrderAudio a)throws Exception {Field f=OrderAudio.class.getDeclaredField("selected");f.setAccessible(true);return (Map<String,File>)f.get(a);}
 static int count(String brand){return WorkApi.counts.getOrDefault(brand,0);}
 static OrderAudio fresh(File dir){WorkApi.failures.clear();WorkApi.counts.clear();WorkApi.downloadMissing=false;WorkApi.wrongBrand=false;SystemClock.now=0;dir.mkdirs();return new OrderAudio(new android.content.Context(dir));}
 static WorkApi.Grant grant(String... brands){WorkApi.Grant g=new WorkApi.Grant();g.brands.addAll(Arrays.asList(brands));return g;}
 public static void main(String[] args)throws Exception {
  File root=new File(args[0]);OrderAudio a=fresh(new File(root,"unsupported"));WorkApi.Grant both=grant("kps","enbishan");
  WorkApi.failures.put("kps",new WorkApi.HttpFailure(404));WorkApi.failures.put("enbishan",new WorkApi.HttpFailure(501));a.refresh(both);
  check(count("kps")==1&&count("enbishan")==1,"first capability probe at boot");
  check(selected(a).get("kps").isFile()&&selected(a).get("enbishan").isFile(),"fallback installed before unavailable metadata");
  check(!Arrays.equals(java.nio.file.Files.readAllBytes(selected(a).get("kps").toPath()),java.nio.file.Files.readAllBytes(selected(a).get("enbishan").toPath())),"brand fallbacks distinct");
  for(int minute=1;minute<360;minute++){SystemClock.now=minute*60000L;a.refresh(both);}
  check(count("kps")==1&&count("enbishan")==1,"404 and501 do not poll each minute");
  WorkApi.failures.remove("kps");SystemClock.now=21600000L;a.refresh(both);
  check(count("kps")==2&&count("enbishan")==2,"bounded six hour capability recheck");
  check(selected(a).get("kps").getName().startsWith("order-ring-kps-"),"automatic supported recovery");
  a.refresh(grant("enbishan"));check(!selected(a).containsKey("kps"),"withdrawn brand selection removed");
  SystemClock.now+=60000;WorkApi.failures.put("kps",new WorkApi.HttpFailure(404));a.refresh(both);
  check(selected(a).get("kps")!=null&&selected(a).get("kps").isFile(),"regranted brand regains fallback even with missing metadata");

  for(Exception failure:new Exception[]{new java.net.SocketTimeoutException("test"),new java.io.IOException("offline"),new WorkApi.HttpFailure(500),new WorkApi.HttpFailure(503)}){
   a=fresh(new File(root,"transient"+cases));WorkApi.Grant g=grant("kps");WorkApi.failures.put("kps",failure);a.refresh(g);File fallback=selected(a).get("kps");
   SystemClock.now=59999;a.refresh(g);check(count("kps")==1,"transient throttled before minute");
   WorkApi.failures.clear();SystemClock.now=60000;a.refresh(g);check(count("kps")==2,"transient retries at minute");
   check(selected(a).get("kps").isFile()&&!selected(a).get("kps").equals(fallback),"transient recovers without deleting fallback");
  }
  a=fresh(new File(root,"asset404"));WorkApi.downloadMissing=true;a.refresh(grant("kps"));
  WorkApi.downloadMissing=false;SystemClock.now=60000;a.refresh(grant("kps"));check(count("kps")==2,"audio asset404 must not disable descriptor endpoint");
  check(selected(a).get("kps").getName().startsWith("order-ring-kps-"),"missing audio recovers at next normal probe");

  a=fresh(new File(root,"cross-brand"));WorkApi.wrongBrand=true;a.refresh(grant("kps"));
  check(selected(a).get("kps").getName().startsWith("approved-order-ring-kps"),"cross brand metadata cannot replace fallback");
  WorkApi.wrongBrand=false;SystemClock.now=60000;a.refresh(grant("kps"));check(count("kps")==2,"invalid descriptor is retryable");

  a=fresh(new File(root,"denied"));WorkApi.failures.put("kps",new WorkApi.Denied());boolean denied=false;
  try{a.refresh(grant("kps"));}catch(WorkApi.Denied e){denied=true;}
  check(denied,"authorization denial must propagate to shift revocation");
  System.out.println("OrderAudio actual refresh integration: "+cases+" PASS");
 }
}
'''



class OrderAudioRefreshIntegration(unittest.TestCase):
    def test_actual_refresh_with_faults(self):
        javac = shutil.which("javac")
        compiler = [javac] if javac else ["java", "--module", "jdk.compiler/com.sun.tools.javac.Main"]
        with tempfile.TemporaryDirectory(prefix="kps-order-audio-test-") as temp:
            root = pathlib.Path(temp)
            for name, content in STUBS.items():
                target=root/name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(content)
            harness=root/"sa/kps/driver/OrderAudioRefreshHarness.java"
            harness.write_text(HARNESS)
            classes=root/"classes"
            classes.mkdir()
            files=list(root.rglob("*.java"))
            files.extend(SOURCES/name for name in ["OrderAudio.java", "SoundRefreshPolicy.java", "SoundPolicy.java", "ApprovedOrderRing.java"])
            subprocess.run([*compiler,"-d",str(classes),*(str(p) for p in files)],check=True)
            subprocess.run(["java","-cp",str(classes),"sa.kps.driver.OrderAudioRefreshHarness",str(root/"cache")],check=True)


if __name__ == "__main__":
    unittest.main()
