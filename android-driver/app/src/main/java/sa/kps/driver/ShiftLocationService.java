package sa.kps.driver;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.location.*;
import android.os.*;
import org.json.JSONObject;
import java.util.concurrent.*;

/** Adapted from TILQA's foreground location-service pattern; KPS APIs remain authoritative.
 * Encrypted offline points remain bound to the original user/shift/session. No reboot/force-stop restart.
 */
public final class ShiftLocationService extends Service implements LocationListener {
    static volatile boolean running=false;
    static volatile String trackingStatus="الموقع متوقف — افتح التطبيق وافحص الجاهزية";
    private OfflineLocationQueue queue;
    static final String CHANNEL="kps-active-shift";
    static final String ORDERS="kps-driver-pending-orders-v1";
    private final BrandBellPolicy bell=new BrandBellPolicy();
    private OrderAudio orderAudio;
    private final Handler main=new Handler(Looper.getMainLooper());
    private final ExecutorService io=Executors.newSingleThreadExecutor();
    private LocationManager locations;
    private volatile WorkApi.Grant bound;
    private volatile boolean ended=false;
    private long lastSample=0;
    private volatile long lastAcknowledgedCapture=0;
    private long serviceStarted=0;
    private boolean busy=false;
    private boolean subscribed=false;
    private final Runnable heartbeat=new Runnable() {
        public void run() {
            if(ended)return;
            if(!busy) {
                busy=true;
                io.execute(()->{
                    int outcome=0;WorkApi.Alerts snapshot=null;
                    try {
                        WorkApi.Grant live=WorkApi.current();
                        if(bound==null||!bound.same(live))throw new WorkApi.Denied();
                        try{orderAudio.refresh(live);}catch(WorkApi.Denied e){throw e;}catch(Exception ignored){}
                        flush(live);
                        snapshot=WorkApi.alerts(live);
                        if(!live.same(WorkApi.current()))throw new WorkApi.Denied();
                        outcome=1;
                    }
                    catch(WorkApi.Denied e){outcome=2;}catch(Exception ignored){}
                    final int result=outcome;
                    final WorkApi.Alerts alerts=snapshot;
                    main.post(()->{busy=false;if(ended)return;if(result==2)revoke();else if(result==1){subscribe();syncBell(alerts);queueStatus();}else offline();});
                });
            }
            main.postDelayed(this,30000);
        }
    };
    @Override public void onCreate() {
        super.onCreate(); queue=new OfflineLocationQueue(this);locations=(LocationManager)getSystemService(LOCATION_SERVICE);orderAudio=new OrderAudio(this);
        NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL,"موقع السائق أثناء الشفت",NotificationManager.IMPORTANCE_LOW));
        NotificationChannel orders=new NotificationChannel(ORDERS,"طلبات السائق التي تنتظر إجراء / Pending driver orders",NotificationManager.IMPORTANCE_HIGH);
        orders.setDescription("يتكرر التنبيه حتى تنفيذ الإجراء / Repeats until action is completed");
        orders.setSound(android.provider.Settings.System.DEFAULT_NOTIFICATION_URI,
            new android.media.AudioAttributes.Builder().setUsage(android.media.AudioAttributes.USAGE_NOTIFICATION).build());
        nm.createNotificationChannel(orders);
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId) {
        if(intent!=null && "STOP".equals(intent.getAction())){revoke();return START_NOT_STICKY;}
        if(intent==null || !"START".equals(intent.getAction()) || checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED) {
            finish(); return START_NOT_STICKY;
        }
        if(running)return START_NOT_STICKY;
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n=new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("KPS Driver").setContentText("مشاركة الموقع أثناء الشفت — تنتهي تلقائيًا عند إقفاله")
            .setOngoing(true).setContentIntent(open).build();
        if(Build.VERSION.SDK_INT>=29)startForeground(7101,n,ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);else startForeground(7101,n);
        running=true;serviceStarted=SystemClock.elapsedRealtime();
        io.execute(()->{
            try {
                WorkApi.Grant grant=WorkApi.current();queue.bind(grant);
                main.post(()->{
                    if(ended)return; bound=grant;
                    subscribe();main.post(heartbeat);
                });
            }catch(WorkApi.Denied e){main.post(this::revoke);}catch(Exception e){main.post(()->{trackingStatus="تعذر بدء التتبع؛ اتصل بالإنترنت وافتح التطبيق";finish();});}
        });
        return START_NOT_STICKY;
    }
    @Override public void onLocationChanged(Location location) {
        long now=SystemClock.elapsedRealtime();
        long age=(SystemClock.elapsedRealtimeNanos()-location.getElapsedRealtimeNanos())/1000000;
        if(ended || bound==null || now-lastSample<20000 || !location.hasAccuracy()
            || !TrackingPolicy.fresh(location.getLatitude(),location.getLongitude(),location.getAccuracy(),age,location.isFromMockProvider()))return;
        lastSample=now;WorkApi.Grant expected=bound;Location point=new Location(location);
        io.execute(()->{
            try {
                if(ended || !expected.cookie.equals(android.webkit.CookieManager.getInstance().getCookie(WorkApi.ORIGIN)))throw new WorkApi.Denied();
                queue.add(expected,point.getTime(),point.getLatitude(),point.getLongitude(),point.getAccuracy());
                main.post(()->{if(!ended)queueStatus();});
            }catch(WorkApi.Denied e){main.post(this::revoke);}
            catch(Exception failure){main.post(()->notification("تعذر حفظ نقاط التتبع؛ افتح التطبيق وافحص الجاهزية"));}
        });
    }
    private void flush(WorkApi.Grant live)throws Exception {
        if(ended || bound==null || !bound.same(live))throw new WorkApi.Denied();
        org.json.JSONArray batch=queue.batch();if(batch.length()==0)return;
        JSONObject result=WorkApi.call("POST","/api/operations/driver-location-batch",live.cookie,live.csrf,
            new JSONObject().put("shift_id",live.shift).put("points",batch));
        // A lost response never drops points: retry uses the same IDs and captured_at.
        if(ended || !live.cookie.equals(android.webkit.CookieManager.getInstance().getCookie(WorkApi.ORIGIN)))throw new WorkApi.Denied();
        org.json.JSONArray accepted=result.getJSONArray("accepted_ids");
        for(int i=0;i<batch.length();i++)for(int j=0;j<accepted.length();j++)if(batch.getJSONObject(i).getString("id").equals(accepted.getString(j)))
            lastAcknowledgedCapture=Math.max(lastAcknowledgedCapture,java.time.Instant.parse(batch.getJSONObject(i).getString("captured_at")).toEpochMilli());
        queue.acknowledge(accepted);
    }
    private void queueStatus(){
        long elapsed=SystemClock.elapsedRealtime();
        if(lastSample==0 || elapsed-lastSample>120000){
            notification(elapsed-serviceStarted<120000?"جارٍ انتظار موقع دقيق؛ شغّل موقع الجهاز":"لم يصل موقع حديث؛ شغّل موقع الجهاز وافتح التطبيق");return;
        }
        int pending=queue.size();
        notification(pending>0?"نقاط محفوظة تنتظر المزامنة: "+pending+" — تحقق من الاتصال":(lastAcknowledgedCapture>0 && System.currentTimeMillis()-lastAcknowledgedCapture<=120000?"موقعك يُشارك — آخر نقطة حديثة":"تمت مزامنة السجل؛ لا يوجد موقع حي حديث"));
        if(queue.dropped()>0)notification("توجد فجوة بالتتبع؛ نقاط منتهية أو تعذر استعادتها — افتح التطبيق");
    }
    private void revoke(){queue.clear();trackingStatus="التتبع متوقف — تحقق من تسجيل الدخول والشفت";finish();}
    private void subscribe(){
        if(ended || subscribed)return;
        if(checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED){revoke();return;}
        try {
            boolean provider=false;
            for(String name:new String[]{LocationManager.GPS_PROVIDER,LocationManager.NETWORK_PROVIDER}) {
                if(locations.isProviderEnabled(name)){locations.requestLocationUpdates(name,25000,0,this,Looper.getMainLooper());provider=true;}
            }
            subscribed=provider;
            notification(provider?"جارٍ تحديد الموقع أثناء الشفت":"شغّل موقع الجهاز؛ ستتم المحاولة تلقائيًا");
        }catch(SecurityException e){revoke();}
    }
    private void offline(){
        // Keep collecting into the original encrypted queue while connectivity is absent.
        // Only an authenticated open matching shift may ingest those samples.
        subscribe();
        bell.invalidate();((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).cancel(7102);
        orderAudio.stop();
        notification("الاتصال متوقف؛ الموقع محفوظ بالجهاز ويزامن عند عودته — افتح التطبيق إذا استمر");
    }
    private void syncBell(WorkApi.Alerts snapshot){
        if(snapshot==null)return;
        long now=SystemClock.elapsedRealtime();
        bell.update(snapshot.pendingByBrand,now,snapshot.policy.optInt("first_after_seconds",0),snapshot.policy.optInt("second_after_seconds",30),snapshot.policy.optInt("continuous_after_seconds",60));
        orderAudio.reconcile(bell.active());
        NotificationManager manager=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        if(bell.count()==0){manager.cancel(7102);orderAudio.stop();return;}
        java.util.List<String> due=bell.due(now);
        if(due.isEmpty()||!manager.areNotificationsEnabled())return;
        if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return;
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        manager.notify(7102,new Notification.Builder(this,ORDERS)
            .setSmallIcon(android.R.drawable.ic_dialog_alert).setContentTitle("KPS Driver")
            .setContentText("طلبات تنتظر إجراء / Orders awaiting action: "+bell.count())
            .setContentIntent(open).setOngoing(true).setAutoCancel(false).setOnlyAlertOnce(true)
            .setGroup("kps-driver-order-audio").setGroupAlertBehavior(Notification.GROUP_ALERT_SUMMARY)
            .setVisibility(Notification.VISIBILITY_PRIVATE).build());
        // No group summary is posted: suppress the duplicate system tone. OrderAudio
        // uses the notification audio stream and honors this same channel's settings.
        orderAudio.play(due);
    }
    private void notification(String body){
        trackingStatus=body;
        if(Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return;
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(7101,
            new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("KPS Driver").setContentText(body).setOngoing(true).setContentIntent(open).build());
    }
    private void finish() {
        if(ended)return;ended=true;bound=null;running=false;
        bell.clear();((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).cancel(7102);
        if(orderAudio!=null)orderAudio.close();
        main.removeCallbacksAndMessages(null);
        try{locations.removeUpdates(this);}catch(SecurityException ignored){}
        stopForeground(STOP_FOREGROUND_REMOVE);stopSelf();
    }
    @Override public void onDestroy(){finish();io.shutdownNow();super.onDestroy();}
    @Override public IBinder onBind(Intent i){return null;}
    @Override public void onProviderDisabled(String provider){subscribed=false;subscribe();}
    @Override public void onProviderEnabled(String provider){subscribed=false;subscribe();}
    @Override public void onStatusChanged(String p,int s,Bundle b){}
}

