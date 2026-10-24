package sa.kps.driver;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.*;
import android.os.*;
import java.io.*;
import org.json.JSONObject;

/** Owner-selected audio is data, never executable code. A single stable channel
 * remains authoritative; do not recreate channels to evade device mute settings. */
final class OrderAudio {
    private final Context context;
    private final Handler main=new Handler(Looper.getMainLooper());
    private volatile File selected;
    private File fallback;
    private long checkedAt=-60000;
    private MediaPlayer player;
    private AudioFocusRequest focus;
    private boolean closed;
    OrderAudio(Context context){this.context=context;}

    // Service's single IO executor only. Never block the main thread on a download.
    void refresh(WorkApi.Grant grant)throws Exception{
        if(fallback==null){
            fallback=new File(context.getCacheDir(),"approved-order-ring.wav");
            try(OutputStream out=new FileOutputStream(fallback)){out.write(ApprovedOrderRing.bytes());}
            selected=fallback;
        }
        long now=SystemClock.elapsedRealtime();
        if(now-checkedAt<60000)return;
        checkedAt=now;
        File temporary=null;
        try{
            JSONObject descriptor=WorkApi.call("GET","/api/operations/driver-order-sound",grant.cookie,null,null).getJSONObject("sound");
            String hash=descriptor.getString("sha256");
            int length=descriptor.getInt("bytes");
            if(!SoundPolicy.descriptor(hash,length,descriptor.getString("path")))throw new IOException("Invalid sound descriptor");
            File destination=new File(context.getCacheDir(),"order-ring-"+hash+".wav");
            if(selected!=null&&selected.equals(destination))return;
            byte[] data=WorkApi.soundBytes(descriptor.getString("path"),grant.cookie);
            if(data.length!=length||!SoundPolicy.digest(data).equals(hash)||!SoundPolicy.wave(data))throw new IOException("Invalid sound data");
            temporary=new File(context.getCacheDir(),"order-ring-download.tmp");
            try(OutputStream out=new FileOutputStream(temporary)){out.write(data);}
            if(!temporary.renameTo(destination))throw new IOException("Cannot publish sound");
            File previous=selected;selected=destination;
            if(previous!=null&&!previous.equals(fallback)&&!previous.equals(destination))previous.delete();
        }catch(WorkApi.Denied e){throw e;}
        catch(Exception ignored){/* Keep approved or last verified audio, never mute on a failed update. */}
        finally{if(temporary!=null)temporary.delete();}
    }
    static boolean audible(Context context){
        NotificationManager nm=context.getSystemService(NotificationManager.class);
        NotificationChannel channel=nm.getNotificationChannel(ShiftLocationService.ORDERS);
        AudioManager audio=context.getSystemService(AudioManager.class);
        return nm.areNotificationsEnabled()
            &&(Build.VERSION.SDK_INT<33||context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)==PackageManager.PERMISSION_GRANTED)
            &&nm.getCurrentInterruptionFilter()==NotificationManager.INTERRUPTION_FILTER_ALL
            &&audio.getRingerMode()==AudioManager.RINGER_MODE_NORMAL
            &&audio.getStreamVolume(AudioManager.STREAM_NOTIFICATION)>0
            &&(channel==null||(channel.getImportance()>=NotificationManager.IMPORTANCE_DEFAULT&&channel.getSound()!=null));
    }
    void play(){
        if(closed||selected==null||player!=null||!audible(context))return;
        AudioManager audio=context.getSystemService(AudioManager.class);
        AudioAttributes attributes=new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build();
        focus=new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(attributes).setOnAudioFocusChangeListener(change->{if(change<0)stop();},main).build();
        if(audio.requestAudioFocus(focus)!=AudioManager.AUDIOFOCUS_REQUEST_GRANTED){stop();return;}
        MediaPlayer next=new MediaPlayer();player=next;
        try{
            next.setAudioAttributes(attributes);next.setDataSource(selected.getAbsolutePath());
            next.setOnPreparedListener(p->{if(player!=p||closed||!audible(context)){stop();return;}p.start();main.post(watch);});
            next.setOnCompletionListener(p->{if(player==p)stop();});
            next.setOnErrorListener((p,a,b)->{if(player==p)stop();return true;});
            next.prepareAsync();
        }catch(Exception ignored){stop();}
    }
    private final Runnable watch=new Runnable(){public void run(){
        if(player==null)return;
        if(!audible(context)){stop();return;}
        main.postDelayed(this,250);
    }};
    void stop(){
        main.removeCallbacks(watch);
        MediaPlayer old=player;player=null;
        if(old!=null){old.setOnPreparedListener(null);old.setOnCompletionListener(null);old.setOnErrorListener(null);old.release();}
        if(focus!=null){context.getSystemService(AudioManager.class).abandonAudioFocusRequest(focus);focus=null;}
    }
    void close(){closed=true;stop();}
}
