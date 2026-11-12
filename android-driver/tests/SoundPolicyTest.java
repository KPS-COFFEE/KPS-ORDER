import sa.kps.driver.ApprovedOrderRing;
import sa.kps.driver.SoundPolicy;
import java.nio.*;
import java.nio.file.*;

public class SoundPolicyTest {
    static int cases;
    static void check(boolean condition){cases++;if(!condition)throw new AssertionError("case "+cases);}
    public static void main(String[] args)throws Exception{
        byte[] approved=ApprovedOrderRing.bytes();
        check(SoundPolicy.wave(approved));check(approved.length==793844);
        String hash=SoundPolicy.digest(approved),path="/api/operations/driver-order-sound/audio/"+hash;
        check(hash.equals("e31f18ed4c2a3ab8acf0950adebc50dade1046e94f70d9eeb1d86e7196dc577c"));
        check(SoundPolicy.descriptor(hash,approved.length,path));
        check(!SoundPolicy.descriptor(hash,approved.length,"https://evil.invalid"+path));
        check(!SoundPolicy.descriptor(hash,approved.length,path+"?token=x"));
        check(!SoundPolicy.descriptor("../x",100,path));
        check(!SoundPolicy.descriptor(hash,1000001,path));
        check(!SoundPolicy.descriptor(hash,0,path));
        check(!SoundPolicy.wave(null));check(!SoundPolicy.wave(new byte[1000001]));
        byte[] bad=approved.clone();bad[0]='P';check(!SoundPolicy.wave(bad));
        bad=approved.clone();bad[20]=3;check(!SoundPolicy.wave(bad));
        bad=approved.clone();bad[22]=2;check(!SoundPolicy.wave(bad));
        bad=approved.clone();bad[34]=8;check(!SoundPolicy.wave(bad));
        bad=approved.clone();bad[36]='L';check(!SoundPolicy.wave(bad));
        bad=approved.clone();bad[40]=(byte)255;bad[41]=(byte)255;bad[42]=(byte)255;bad[43]=(byte)255;check(!SoundPolicy.wave(bad));
        bad=approved.clone();bad[4]=1;check(!SoundPolicy.wave(bad));
        bad=new byte[46];ByteBuffer.wrap(bad).order(ByteOrder.LITTLE_ENDIAN).putInt(4,38);
        System.arraycopy(approved,0,bad,0,44);ByteBuffer.wrap(bad).order(ByteOrder.LITTLE_ENDIAN).putInt(4,38).putInt(40,2);
        check(!SoundPolicy.wave(bad));
        if(args.length>0){byte[] expected=Files.readAllBytes(Path.of(args[0]));check(java.util.Arrays.equals(approved,expected));}
        System.out.println("SoundPolicy: "+cases+" PASS; approved SHA-256 "+hash);
    }
}
