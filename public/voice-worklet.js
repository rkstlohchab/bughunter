class LocalVoiceCapture extends AudioWorkletProcessor {
 constructor(){super();this.phase=0;this.sum=0;this.count=0;this.samples=[];}
 process(inputs){const channel=inputs[0]?.[0];if(!channel)return true;
  for(const value of channel){this.sum+=value;this.count++;this.phase+=16000/sampleRate;
   if(this.phase>=1){this.samples.push(Math.max(-32768,Math.min(32767,Math.round((this.sum/this.count)*32767))));this.phase-=1;this.sum=0;this.count=0;}
   if(this.samples.length===8000){const pcm=new Int16Array(this.samples);this.port.postMessage(pcm.buffer,[pcm.buffer]);this.samples=[];}
  }return true;
 }
}
registerProcessor('local-voice-capture',LocalVoiceCapture);
