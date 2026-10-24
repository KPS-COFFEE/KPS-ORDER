package sa.kps.driver;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/** Strict, bounded PCM WAV data only. No URI, script, archive or arbitrary URL. */
public final class SoundPolicy {
    public static final int MAX_BYTES=1000000;
    public static boolean descriptor(String hash,int bytes,String path){
        return hash!=null&&hash.matches("[a-f0-9]{64}")&&bytes>=46&&bytes<=MAX_BYTES
            &&("/api/operations/driver-order-sound/audio/"+hash).equals(path);
    }
    public static String digest(byte[] data)throws Exception{
        byte[] hash=MessageDigest.getInstance("SHA-256").digest(data);
        StringBuilder result=new StringBuilder();for(byte b:hash)result.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
        return result.toString();
    }
    private static String tag(byte[] data,int at){return new String(data,at,4,StandardCharsets.US_ASCII);}
    public static boolean wave(byte[] data){
        if(data==null||data.length<46||data.length>MAX_BYTES||!tag(data,0).equals("RIFF")||!tag(data,8).equals("WAVE"))return false;
        ByteBuffer b=ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN);
        if(Integer.toUnsignedLong(b.getInt(4))+8!=data.length)return false;
        int rate=0,block=0,frames=0;boolean format=false,audio=false;
        for(int at=12;at+8<=data.length;){
            long size=Integer.toUnsignedLong(b.getInt(at+4));long end=at+8L+size;
            if(end>data.length)return false;
            String kind=tag(data,at);
            if(kind.equals("fmt ")){
                if(format||size!=16||b.getShort(at+8)!=1||b.getShort(at+10)!=1||b.getShort(at+22)!=16)return false;
                rate=b.getInt(at+12);block=b.getShort(at+20);
                if((rate!=22050&&rate!=44100&&rate!=48000)||block!=2||b.getInt(at+16)!=rate*block)return false;
                format=true;
            }else if(kind.equals("data")){
                if(audio||size<2||size%2!=0)return false;frames=(int)size/2;audio=true;
            }else return false; // No metadata or hidden payloads.
            at=(int)(end+(size&1));if(at==data.length)break;
            if(at+8>data.length)return false;
        }
        return format&&audio&&frames>=rate/10&&frames<=rate*10;
    }
}
