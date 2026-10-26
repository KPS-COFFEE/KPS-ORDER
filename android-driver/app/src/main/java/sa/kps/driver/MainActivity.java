package sa.kps.driver;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.*;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.*;
import android.widget.*;
import java.util.concurrent.*;

/** The existing KPS driver workflow, with no JavaScript credential/native bridge. */
public final class MainActivity extends Activity {
    private WebView web;
    private TextView status;
    private Button retry;
    private final Handler main=new Handler(Looper.getMainLooper());
    private final ExecutorService io=Executors.newSingleThreadExecutor();
    private boolean visible=false,checking=false,permissionAsked=false,notificationsAsked=false;
    private ValueCallback<Uri[]> upload;
    private final Runnable poll=new Runnable(){public void run(){if(visible){checkShift();main.postDelayed(this,15000);}}};
    @Override public void onCreate(Bundle saved){
        super.onCreate(saved);WebView.setWebContentsDebuggingEnabled(false);
        LinearLayout layout=new LinearLayout(this);layout.setOrientation(LinearLayout.VERTICAL);
        if(Build.VERSION.SDK_INT>=30){
            // SDK 35 draws below system bars and camera cutouts. Keep both the
            // readiness control and WebView inside the usable window, including
            // landscape cutouts and the on-screen keyboard.
            getWindow().setDecorFitsSystemWindows(false);
            layout.setOnApplyWindowInsetsListener((view,insets)->{
                android.graphics.Insets safe=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout()|WindowInsets.Type.ime());
                view.setPadding(safe.left,safe.top,safe.right,safe.bottom);
                return WindowInsets.CONSUMED;
            });
        }else layout.setFitsSystemWindows(true);
        int pad=(int)(12*getResources().getDisplayMetrics().density+0.5f);
        LinearLayout bar=new LinearLayout(this);bar.setOrientation(LinearLayout.VERTICAL);bar.setPadding(pad,pad/2,pad,pad/2);
        status=new TextView(this);status.setText("افتح شفتك كالمعتاد؛ الموقع يعمل أثناء الشفت فقط");
        retry=new Button(this);retry.setText("فحص الجاهزية / Check readiness");retry.setOnClickListener(v->{
            permissionAsked=false;notificationsAsked=false;
            if(!alertsAudible()){
                NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
                Intent settings=new Intent(nm.getNotificationChannel(ShiftLocationService.ORDERS)==null?android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS:android.provider.Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
                    .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE,getPackageName()).putExtra(android.provider.Settings.EXTRA_CHANNEL_ID,ShiftLocationService.ORDERS);
                startActivity(settings);
            }else checkShift();
        });
        bar.addView(status,new LinearLayout.LayoutParams(-1,-2));
        retry.setMinHeight((int)(48*getResources().getDisplayMetrics().density+0.5f));
        bar.addView(retry,new LinearLayout.LayoutParams(-1,-2));layout.addView(bar);
        web=new WebView(this);layout.addView(web,new LinearLayout.LayoutParams(-1,0,1));setContentView(layout);
        layout.requestApplyInsets();
        CookieManager.getInstance().setAcceptCookie(true);CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
        WebSettings s=web.getSettings();s.setJavaScriptEnabled(true);s.setDomStorageEnabled(true);
        s.setUserAgentString(s.getUserAgentString()+" KPSDriver/0.2.4 KPSNativeOrderAudio/1");
        s.setAllowFileAccess(false);s.setAllowContentAccess(false);s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setGeolocationEnabled(false); // Native service owns GPS; never a second browser watcher.
        web.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest r){
                Uri u=r.getUrl();
                if(isWork(u))return false;
                if(r.isForMainFrame() && r.hasGesture() && "https".equals(u.getScheme())) {
                    try{startActivity(new Intent(Intent.ACTION_VIEW,u));}catch(ActivityNotFoundException ignored){}
                }
                return true;
            }
            @Override public void onPageFinished(WebView v,String url){
                if(!isWork(Uri.parse(url))){web.stopLoading();return;}
                // Hide only the redundant browser-only GPS panel inside this app.
                web.evaluateJavascript("(()=>{const p=document.getElementById('driver-location');if(p)p.style.display='none';})()",null);
                checkShift();
            }
            @Override public void onReceivedSslError(WebView v,android.webkit.SslErrorHandler h,android.net.http.SslError e){h.cancel();status.setText("تعذر الاتصال الآمن؛ أعد المحاولة لاحقًا");}
        });
        web.setWebChromeClient(new WebChromeClient(){
            @Override public boolean onShowFileChooser(WebView v,ValueCallback<Uri[]> result,FileChooserParams p){
                if(upload!=null)upload.onReceiveValue(null);upload=result;
                Intent pick=new Intent(Intent.ACTION_GET_CONTENT).setType("image/*").addCategory(Intent.CATEGORY_OPENABLE);
                try{startActivityForResult(Intent.createChooser(pick,"اختر صورة الإثبات"),31);}catch(ActivityNotFoundException e){upload.onReceiveValue(null);upload=null;}
                return true;
            }
            @Override public void onPermissionRequest(PermissionRequest r){r.deny();}
        });
        web.loadUrl(WorkApi.ORIGIN+"/ops");
    }
    static boolean isWork(Uri u){return "https".equals(u.getScheme()) && "order.kps.sa".equals(u.getHost()) && (u.getPort()==-1 || u.getPort()==443);}
    private void checkShift(){
        if(!visible || checking)return;checking=true;
        io.execute(()->{
            boolean active=false,denied=false;
            try{WorkApi.current();active=true;}catch(WorkApi.Denied e){denied=true;}catch(Exception ignored){}
            final boolean authorized=active,revoked=denied;
            main.post(()->{
                checking=false;if(!visible)return;
                if(!authorized){
                    if(revoked){
                        if(ShiftLocationService.running)startService(new Intent(this,ShiftLocationService.class).setAction("STOP"));
                        else new OfflineLocationQueue(this).clear();
                    }
                    status.setText(revoked?"الموقع متوقف — سجّل الدخول وافتح شفتك":"تعذر التحقق من الاتصال؛ جارٍ المحاولة");return;
                }
                if(checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED){
                    status.setText("يلزم السماح بالموقع الدقيق أول مرة");
                    if(!permissionAsked){permissionAsked=true;new AlertDialog.Builder(this).setTitle("موقع السائق أثناء الشفت")
                        .setMessage("يظهر موقعك لمدير التوصيل أثناء الشفت المفتوح، ويستمر عند قفل الشاشة أو استخدام واتساب. يتوقف عند إقفال الشفت. اسمح بالموقع الدقيق في الخطوة التالية.")
                        .setPositiveButton("متابعة",(d,w)->requestPermissions(new String[]{Manifest.permission.ACCESS_COARSE_LOCATION,Manifest.permission.ACCESS_FINE_LOCATION},10))
                        .setNegativeButton("لاحقًا",null).show();}return;
                }
                if(Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED && !notificationsAsked){
                    notificationsAsked=true;requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},11);return;
                }
                if(!ShiftLocationService.running){
                    try{startForegroundService(new Intent(this,ShiftLocationService.class).setAction("START"));}
                    catch(RuntimeException e){status.setText("افتح التطبيق لإعادة تشغيل الموقع");return;}
                }
                status.setText(alertsAudible()?ShiftLocationService.trackingStatus:"تنبيهات الطلبات قد لا تُسمع؛ راجع إعدادات الجهاز / Check device sound and notification settings");
            });
        });
    }
    private boolean alertsAudible(){
        return OrderAudio.audible(this);
    }
    @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] grants){super.onRequestPermissionsResult(code,permissions,grants);checkShift();}
    @Override protected void onResume(){super.onResume();visible=true;main.removeCallbacks(poll);main.post(poll);}
    @Override protected void onPause(){visible=false;main.removeCallbacks(poll);CookieManager.getInstance().flush();super.onPause();}
    @Override protected void onDestroy(){visible=false;main.removeCallbacksAndMessages(null);if(upload!=null)upload.onReceiveValue(null);web.destroy();io.shutdownNow();super.onDestroy();}
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(request==31 && upload!=null){upload.onReceiveValue(result==RESULT_OK && data!=null && data.getData()!=null?new Uri[]{data.getData()}:null);upload=null;}}
    @Override public void onBackPressed(){if(web.canGoBack())web.goBack();else super.onBackPressed();}
}

