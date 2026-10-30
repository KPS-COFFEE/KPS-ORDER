package sa.kps.driver;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.*;
import android.os.*;
import java.io.*;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import org.json.JSONObject;

/** One stable device-controlled channel, independent brand files, no overlapping audio. */
final class OrderAudio {
    private final Context context;
    private final Handler main=new Handler(Looper.getMainLooper());
    private final Map<String,File> selected=new ConcurrentHashMap<>();
    private final Map<String,File> fallbacks=new HashMap<>();
    private final SoundRefreshPolicy refreshPolicy=new SoundRefreshPolicy();
    private final Deque<String> queue=new ArrayDeque<>();
    private Set<String> active=new HashSet<>();
    private long verified=-1;
    private String playingBrand;
    private MediaPlayer player;
    private AudioFocusRequest focus;
    private boolean closed;
    OrderAudio(Context context){this.context=context;}

    // Single IO executor only; never persist credentials alongside audio.
    void refresh(WorkApi.Grant grant)throws Exception{
        selected.keySet().retainAll(grant.brands);
        refreshPolicy.retain(grant.brands);
        for(String brand:new TreeSet<>(grant.brands))refreshBrand(grant,brand);
    }
    private void refreshBrand(WorkApi.Grant grant,String brand)throws Exception{
        File fallback=fallbacks.get(brand);
        if(fallback==null||!fallback.isFile()){
            fallback=new File(context.getCacheDir(),"approved-order-ring-"+brand+".wav");
            try(OutputStream out=new FileOutputStream(fallback)){out.write(ApprovedOrderRing.bytes(brand));}
            fallbacks.put(brand,fallback);
        }
        File old=selected.get(brand);if(old==null||!old.isFile())selected.put(brand,fallback);
        long now=SystemClock.elapsedRealtime();
        if(!refreshPolicy.begin(brand,now))return;
        File temporary=null;
        try{
            JSONObject response;
            try{
                response=WorkApi.call("GET","/api/operations/driver-order-sound?brand_id="+brand,grant.cookie,null,null);
            }catch(WorkApi.HttpFailure e){
                // Missing optional metadata is distinct from failed audio downloads or access denial.
                refreshPolicy.unavailable(brand,SystemClock.elapsedRealtime(),e.status);
                throw e;
            }
            JSONObject descriptor=response.getJSONObject("sound");
            String hash=descriptor.getString("sha256"),path=descriptor.getString("path");int length=descriptor.getInt("bytes");
            if(!brand.equals(descriptor.getString("brand_id"))||!SoundPolicy.descriptor(hash,length,path,brand))throw new IOException("Invalid brand sound descriptor");
            File destination=new File(context.getCacheDir(),"order-ring-"+brand+"-"+hash+".wav");
            if(destination.equals(selected.get(brand))&&destination.isFile()&&destination.length()==length)return;
            byte[] data=WorkApi.soundBytes(path,grant.cookie);
            if(data.length!=length||!SoundPolicy.digest(data).equals(hash)||!SoundPolicy.wave(data))throw new IOException("Invalid sound data");
            temporary=new File(context.getCacheDir(),"order-ring-"+brand+"-download.tmp");
            try(OutputStream out=new FileOutputStream(temporary)){out.write(data);}
            if(!temporary.renameTo(destination))throw new IOException("Cannot publish sound");
            File previous=selected.put(brand,destination);
            if(previous!=null&&!previous.equals(fallback)&&!previous.equals(destination))previous.delete();
        }catch(WorkApi.Denied e){throw e;}
        catch(Exception ignored){/* Retain this brand's ring, never substitute another brand. */}
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
    void reconcile(Set<String> pendingBrands){
        active=new HashSet<>(pendingBrands);verified=SystemClock.elapsedRealtime();queue.retainAll(active);
        if(playingBrand!=null&&!active.contains(playingBrand)){stopCurrent();playNext();}
    }
    void play(List<String> brands){
        if(closed)return;
        for(String brand:brands)if(active.contains(brand)&&!brand.equals(playingBrand)&&!queue.contains(brand))queue.addLast(brand);
        playNext();
    }
    private boolean fresh(){long now=SystemClock.elapsedRealtime();return verified>=0&&now>=verified&&now-verified<=30000;}
    private void playNext(){
        if(player!=null||closed)return;
        if(!fresh()||!audible(context)){stop();return;}
        String brand=null;File file=null;
        while(!queue.isEmpty()){
            String candidate=queue.removeFirst();File candidateFile=selected.get(candidate);
            if(active.contains(candidate)&&candidateFile!=null&&candidateFile.isFile()){brand=candidate;file=candidateFile;break;}
        }
        if(file==null)return;
        playingBrand=brand;
        AudioManager audio=context.getSystemService(AudioManager.class);
        AudioAttributes attributes=new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build();
        focus=new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(attributes).setOnAudioFocusChangeListener(change->{if(change<0)stop();},main).build();
        if(audio.requestAudioFocus(focus)!=AudioManager.AUDIOFOCUS_REQUEST_GRANTED){stop();return;}
        MediaPlayer next=new MediaPlayer();player=next;
        try{
            next.setAudioAttributes(attributes);next.setDataSource(file.getAbsolutePath());
            next.setOnPreparedListener(p->{if(player!=p)return;if(closed||!fresh()||!audible(context)){stop();return;}p.start();main.post(watch);});
            next.setOnCompletionListener(p->{if(player==p){stopCurrent();playNext();}});
            next.setOnErrorListener((p,a,b)->{if(player==p){stopCurrent();playNext();}return true;});
            next.prepareAsync();
        }catch(Exception ignored){stopCurrent();playNext();}
    }
    private final Runnable watch=new Runnable(){public void run(){
        if(player==null)return;
        if(!fresh()||!audible(context)){stop();return;}
        main.postDelayed(this,250);
    }};
    private void stopCurrent(){
        main.removeCallbacks(watch);MediaPlayer old=player;player=null;playingBrand=null;
        if(old!=null){old.setOnPreparedListener(null);old.setOnCompletionListener(null);old.setOnErrorListener(null);old.release();}
        if(focus!=null){context.getSystemService(AudioManager.class).abandonAudioFocusRequest(focus);focus=null;}
    }
    void stop(){queue.clear();stopCurrent();}
    void close(){closed=true;active.clear();verified=-1;stop();}
}
