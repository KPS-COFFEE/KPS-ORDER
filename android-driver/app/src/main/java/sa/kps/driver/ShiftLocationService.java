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
 * No coordinates or copied credentials are written to disk. No reboot/force-stop restart.
 */
public final class ShiftLocationService extends Service implements LocationListener {
    static volatile boolean running=false;
    static final String CHANNEL="kps-active-shift";
    private final Handler main=new Handler(Looper.getMainLooper());
    private final ExecutorService io=Executors.newSingleThreadExecutor();
    private LocationManager locations;
    private volatile WorkApi.Grant bound;
    private volatile boolean ended=false;
    private long lastSample=0;
    private boolean busy=false;
    private boolean subscribed=false;
    private final Runnable heartbeat=new Runnable() {
        public void run() {
            if(ended)return;
            if(!busy) {
                busy=true;
                io.execute(()->{
                    int outcome=0;
                    try { outcome=bound!=null && bound.same(WorkApi.current())?1:2; }
                    catch(WorkApi.Denied e){outcome=2;}catch(Exception ignored){}
                    final int result=outcome;
                    main.post(()->{busy=false;if(ended)return;if(result==2)finish();else if(result==1)subscribe();else pause();});
                });
            }
            main.postDelayed(this,30000);
        }
    };
    @Override public void onCreate() {
        super.onCreate(); locations=(LocationManager)getSystemService(LOCATION_SERVICE);
        NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL,"موقع السائق أثناء الشفت",NotificationManager.IMPORTANCE_LOW));
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId) {
        if(intent==null || !"START".equals(intent.getAction()) || checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED) {
            finish(); return START_NOT_STICKY;
        }
        if(running)return START_NOT_STICKY;
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n=new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("KPS Driver").setContentText("مشاركة الموقع أثناء الشفت — تنتهي تلقائيًا عند إقفاله")
            .setOngoing(true).setContentIntent(open).build();
        if(Build.VERSION.SDK_INT>=29)startForeground(7101,n,ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);else startForeground(7101,n);
        running=true;
        io.execute(()->{
            try {
                WorkApi.Grant grant=WorkApi.current();
                main.post(()->{
                    if(ended)return; bound=grant;
                    subscribe();main.postDelayed(heartbeat,30000);
                });
            }catch(Exception e){main.post(this::finish);}
        });
        return START_NOT_STICKY;
    }
    @Override public void onLocationChanged(Location location) {
        long now=SystemClock.elapsedRealtime();
        long age=(SystemClock.elapsedRealtimeNanos()-location.getElapsedRealtimeNanos())/1000000;
        if(ended || bound==null || busy || now-lastSample<20000 || !location.hasAccuracy()
            || !TrackingPolicy.fresh(location.getLatitude(),location.getLongitude(),location.getAccuracy(),age,location.isFromMockProvider()))return;
        lastSample=now;busy=true;WorkApi.Grant expected=bound;
        Location point=new Location(location);
        io.execute(()->{
            int outcome=1;
            try {
                WorkApi.Grant live=WorkApi.current();
                if(ended || !expected.same(live))throw new WorkApi.Denied();
                long currentAge=(SystemClock.elapsedRealtimeNanos()-point.getElapsedRealtimeNanos())/1000000;
                if(currentAge>30000)return;
                WorkApi.call("POST","/api/operations/driver-location",live.cookie,live.csrf,
                    new JSONObject().put("lat",point.getLatitude()).put("lng",point.getLongitude()).put("accuracy_m",(double)point.getAccuracy()));
            }catch(WorkApi.Denied e){outcome=2;}catch(Exception e){outcome=0;}
            finally {final int result=outcome;main.post(()->{busy=false;if(ended)return;if(result==2)finish();else if(result==0)pause();else notification("موقعك يُشارك — آخر تحديث الآن");});}
        });
    }
    private void subscribe(){
        if(ended || subscribed)return;
        if(checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED){finish();return;}
        try {
            boolean provider=false;
            for(String name:new String[]{LocationManager.GPS_PROVIDER,LocationManager.NETWORK_PROVIDER}) {
                if(locations.isProviderEnabled(name)){locations.requestLocationUpdates(name,25000,0,this,Looper.getMainLooper());provider=true;}
            }
            subscribed=provider;
            notification(provider?"جارٍ تحديد الموقع أثناء الشفت":"شغّل موقع الجهاز؛ ستتم المحاولة تلقائيًا");
        }catch(SecurityException e){finish();}
    }
    private void pause(){
        try{locations.removeUpdates(this);}catch(SecurityException ignored){}
        subscribed=false;
        notification("تعذر التحديث؛ ستتم المحاولة تلقائيًا عند عودة الاتصال");
    }
    private void notification(String body){
        if(Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return;
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(7101,
            new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("KPS Driver").setContentText(body).setOngoing(true).setContentIntent(open).build());
    }
    private void finish() {
        if(ended)return;ended=true;bound=null;running=false;
        main.removeCallbacksAndMessages(null);
        try{locations.removeUpdates(this);}catch(SecurityException ignored){}
        stopForeground(STOP_FOREGROUND_REMOVE);stopSelf();
    }
    @Override public void onDestroy(){finish();io.shutdownNow();super.onDestroy();}
    @Override public IBinder onBind(Intent i){return null;}
    @Override public void onProviderDisabled(String provider){}
    @Override public void onProviderEnabled(String provider){}
    @Override public void onStatusChanged(String p,int s,Bundle b){}
}
